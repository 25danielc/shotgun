"""Render comp/index.html frame by frame (window.seek(t)) and encode with the mix.

    uv run --with playwright python video/tools/render.py --probe 1.2,10.5      # PNG probes
    uv run --with playwright python video/tools/render.py --probe-every 2.0      # a probe per 2 s
    uv run --with playwright python video/tools/render.py                        # the full video
    uv run --with playwright python video/tools/render.py --from 50 --to 60      # a range

Files are served from video/ through a Playwright route (no server, nothing leaves the
machine). Frames go to ffmpeg over a pipe; with --audio the mix is muxed in.
"""

from __future__ import annotations

import argparse
import asyncio
import mimetypes
import subprocess
import time
from pathlib import Path

VIDEO = Path(__file__).resolve().parents[1]
FPS = 30


async def open_page(p):
    browser = await p.chromium.launch()
    ctx = await browser.new_context(viewport={"width": 1920, "height": 1080}, device_scale_factor=1)
    page = await ctx.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)

    async def route(r):
        rel = r.request.url.split("://", 1)[1].split("/", 1)[1].split("?")[0]
        f = VIDEO / rel
        if not f.is_file():
            await r.fulfill(status=404, body="")
            return
        ctype = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
        await r.fulfill(status=200, body=f.read_bytes(), headers={"content-type": ctype})

    await page.route("http://comp.local/**", route)
    await page.goto("http://comp.local/comp/index.html")
    await page.wait_for_function("window.seek && document.fonts.status === 'loaded'")
    return browser, page, errors


async def probes(times, out: Path):
    from playwright.async_api import async_playwright

    out.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as p:
        browser, page, errors = await open_page(p)
        for t in times:
            await page.evaluate(f"window.seek({t})")
            await page.screenshot(path=str(out / f"t{t:07.3f}.png"))
        await browser.close()
    if errors:
        print("page errors:", *sorted(set(errors))[:10], sep="\n  ")
    print(f"{len(times)} probes in {out}")


async def render(t0, t1, dest: Path, audio: Path | None):
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser, page, errors = await open_page(p)
        duration = await page.evaluate("window.DURATION")
        t1 = min(t1 if t1 is not None else duration, duration)
        n0, n1 = round(t0 * FPS), round(t1 * FPS)
        cmd = ["ffmpeg", "-v", "error", "-y", "-f", "image2pipe", "-framerate", str(FPS),
               "-c:v", "png", "-i", "-"]  # fmt: skip
        if audio:
            cmd += ["-ss", f"{t0:.4f}", "-t", f"{(n1 - n0) / FPS:.4f}", "-i", str(audio)]
        cmd += ["-c:v", "libx264", "-profile:v", "high", "-preset", "slow", "-crf", "18",
                "-pix_fmt", "yuv420p", "-r", str(FPS), "-movflags", "+faststart"]  # fmt: skip
        if audio:
            cmd += ["-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-shortest"]
        cmd += [str(dest)]
        enc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
        started = time.time()
        for n in range(n0, n1):
            await page.evaluate(f"window.seek({n / FPS})")
            enc.stdin.write(await page.screenshot(type="png"))
            if (n - n0) % 150 == 0:
                print(f"frame {n}/{n1} ({time.time() - started:.0f}s)", flush=True)
        enc.stdin.close()
        enc.wait()
        await browser.close()
    if errors:
        print("page errors:", *sorted(set(errors))[:10], sep="\n  ")
    print("wrote", dest)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--probe", help="comma-separated global seconds")
    ap.add_argument("--probe-every", type=float)
    ap.add_argument("--probe-dir", default=str(VIDEO / "work" / "probes"))
    ap.add_argument("--from", dest="t0", type=float, default=0.0)
    ap.add_argument("--to", dest="t1", type=float)
    ap.add_argument("--audio", default=str(VIDEO / "work" / "audio" / "mix.wav"))
    ap.add_argument("--no-audio", action="store_true")
    ap.add_argument("--out", default=str(VIDEO / "out" / "shotgun_demo_1080p.mp4"))
    a = ap.parse_args()
    if a.probe or a.probe_every:
        times = [float(x) for x in a.probe.split(",")] if a.probe else []
        if a.probe_every:
            k = 0.0
            while k < 99.8:
                times.append(round(k, 3))
                k += a.probe_every
        asyncio.run(probes(times, Path(a.probe_dir)))
        return
    audio = None if a.no_audio else Path(a.audio)
    asyncio.run(render(a.t0, a.t1, Path(a.out), audio if audio and audio.exists() else None))


if __name__ == "__main__":
    main()
