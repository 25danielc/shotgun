"""Screenshot comp/thumb.html (1280x720) to out/thumbnail.png.

uv run --with playwright python video/tools/thumb.py
"""

import asyncio
import mimetypes
from pathlib import Path

VIDEO = Path(__file__).resolve().parents[1]


async def main():
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(viewport={"width": 1280, "height": 720})

        async def route(r):
            rel = r.request.url.split("://", 1)[1].split("/", 1)[1].split("?")[0]
            f = VIDEO / rel
            if not f.is_file():
                await r.fulfill(status=404, body="")
                return
            ctype = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
            await r.fulfill(status=200, body=f.read_bytes(), headers={"content-type": ctype})

        await page.route("http://comp.local/**", route)
        await page.goto("http://comp.local/comp/thumb.html")
        await page.wait_for_load_state("networkidle")
        await page.evaluate("document.fonts.ready")
        await page.screenshot(path=str(VIDEO / "out" / "thumbnail.png"))
        await browser.close()


asyncio.run(main())
