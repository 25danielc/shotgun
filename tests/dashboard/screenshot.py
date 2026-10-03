# /// script
# requires-python = ">=3.12"
# dependencies = ["playwright"]
# ///
"""Screenshot static/dashboard.html in mock mode at the two judging-table sizes.

Not a pytest test: run it by hand after changing the dashboard, then look at the images
against .claude/skills/shotgun-dashboard-design/SKILL.md.

    uvx playwright install chromium            # once
    uv run tests/dashboard/screenshot.py [--out DIR] [--wait SECONDS]

Serves the repo root statically (so ?mock= can fetch tests/fixtures/) and writes
dashboard_<mock>_<w>x<h>.png for ?mock=1 (mid-drive), ?mock=empty and ?mock=edge (nulls, long
text, every state: a robustness check) at 1440x900 and 1280x800. Exits 1 on any JS error or
console error. Offline: no server, database or API is touched (Google Fonts are fetched if
reachable; the page falls back to ui-monospace).
"""

from __future__ import annotations

import argparse
import functools
import http.server
import tempfile
import threading
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
MOCKS = {"1": "mock", "empty": "empty", "edge": "edge"}
SIZES = ((1440, 900), (1280, 800))


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args) -> None:
        pass


def serve() -> http.server.ThreadingHTTPServer:
    handler = functools.partial(QuietHandler, directory=str(ROOT))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--out", type=Path, default=Path(tempfile.gettempdir()) / "shotgun-dashboard"
    )
    parser.add_argument(
        "--wait", type=float, default=2.5, help="seconds after load (mock story time)"
    )
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    server = serve()
    base = f"http://127.0.0.1:{server.server_port}/static/dashboard.html"
    errors: list[str] = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        for mock, name in MOCKS.items():
            for width, height in SIZES:
                page = browser.new_page(viewport={"width": width, "height": height})
                where = f"{name} {width}x{height}"
                page.on("pageerror", lambda exc, w=where: errors.append(f"{w}: {exc}"))
                page.on(
                    "console",
                    lambda msg, w=where: (
                        errors.append(f"{w}: console {msg.text}")
                        if msg.type == "error" and "fonts.g" not in msg.text
                        else None
                    ),
                )
                page.goto(f"{base}?mock={mock}")
                page.evaluate("document.fonts.ready")
                page.wait_for_timeout(args.wait * 1000)
                if page.locator("text=[ OFFLINE ]").count():  # poll() caught a render error
                    errors.append(f"{where}: page shows OFFLINE: " + page.inner_text("#b-sys"))
                scroll = page.evaluate(
                    "[document.documentElement.scrollWidth > innerWidth,"
                    " document.documentElement.scrollHeight > innerHeight]"
                )
                path = args.out / f"dashboard_{name}_{width}x{height}.png"
                page.screenshot(path=str(path))
                note = "  PAGE SCROLLS" if any(scroll) else ""
                print(f"{path}{note}")
                page.close()
        browser.close()
    server.shutdown()
    for error in errors:
        print(f"JS error: {error}")
    if errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
