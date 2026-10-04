"""Record the live dashboard for the demo video (step 7.2): a clean capture, no browser chrome.

    make record-dashboard              # 30 min, or until stopped
    make record-dashboard MIN=45
    touch ~/Movies/shotgun/STOP        # stop early (or Ctrl-C); files are finalized either way

A headless Chromium opens PUBLIC_BASE_URL/dashboard#token=... at 1440x900 and writes, into
~/Movies/shotgun/<date-time>/:
    dashboard.mp4          the smooth recording (Playwright video, 1440x900): countdowns, motion
    dashboard_sharp.mp4    one 2x frame per second (2880x1800), real time: crisp text for zooms
    frames/frame_NNNNN.png those frames, for stills
Tested 2026-10-03: the video is slightly soft on 11 px text; the 2x frames are crisp, so both are
kept. Needs ffmpeg (brew) for the MP4s; without it the .webm and PNGs are still written.
The token never appears in a file or in the output.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import settings  # noqa: E402

OUT_ROOT = Path.home() / "Movies" / "shotgun"
STOP = OUT_ROOT / "STOP"
VIEWPORT = {"width": 1440, "height": 900}


def ffmpeg(*args: str) -> bool:
    if not shutil.which("ffmpeg"):
        return False
    done = subprocess.run(["ffmpeg", "-loglevel", "error", "-y", *args], check=False)
    return done.returncode == 0


def record(minutes: float, out: Path) -> int:
    from playwright.sync_api import sync_playwright

    if not (settings.public_base_url and settings.dashboard_token):
        sys.exit("needs PUBLIC_BASE_URL and DASHBOARD_TOKEN in .env")
    frames = out / "frames"
    frames.mkdir(parents=True, exist_ok=True)
    STOP.unlink(missing_ok=True)
    shots = 0
    with sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context(
            viewport=VIEWPORT,
            device_scale_factor=2,
            record_video_dir=str(out),
            record_video_size=VIEWPORT,
        )
        page = context.new_page()
        page.goto(
            f"{settings.public_base_url.rstrip('/')}/dashboard#token={settings.dashboard_token}"
        )
        print(f"recording to {out} for up to {minutes:g} min (touch {STOP} to stop)", flush=True)
        end = time.time() + minutes * 60
        try:
            while time.time() < end and not STOP.exists():
                started = time.time()
                page.screenshot(path=str(frames / f"frame_{shots:05d}.png"))
                shots += 1
                time.sleep(max(0.0, 1.0 - (time.time() - started)))
        except KeyboardInterrupt:
            pass
        video = page.video.path() if page.video else None
        context.close()
        browser.close()
    STOP.unlink(missing_ok=True)
    if video and ffmpeg(
        "-i",
        str(video),
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-crf",
        "16",
        str(out / "dashboard.mp4"),
    ):
        Path(video).unlink(missing_ok=True)
    ffmpeg(
        "-framerate",
        "1",
        "-i",
        str(frames / "frame_%05d.png"),
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-crf",
        "16",
        "-r",
        "30",
        str(out / "dashboard_sharp.mp4"),
    )
    print(f"done: {shots} frames, files in {out}", flush=True)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--minutes", type=float, default=30.0)
    args = parser.parse_args()
    out = OUT_ROOT / datetime.now().strftime("%Y-%m-%d_%H%M%S")
    return record(args.minutes, out)


if __name__ == "__main__":
    sys.exit(main())
