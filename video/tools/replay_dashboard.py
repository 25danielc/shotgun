"""Replay drive #8 (2026-10-03, 22:25) through the real dashboard, frame-exact, for the video.

    uv run --with playwright python video/tools/replay_dashboard.py            # both segments
    uv run --with playwright python video/tools/replay_dashboard.py --probe 22:28:33

Every value comes from that drive:
- **Neon rows**, cached read-only in video/work/replay_drive8.json: the drive, its calls, jobs
  #189 and #190, and their job_events.
- **The six tool calls** as the live dashboard's TOOLS window recorded them (ToolCallRecorder
  start time, server latency, query; see video/STUDY.md).
- **Call connect and end times** from the dashboard LOG and ElevenLabs.
- **Service probe latencies** read off the live recording.

What is replayed:
- app.dashboard.build_state(now=T) runs unchanged on the rows as they stood at T. Its _read is
  swapped for an as-of-T view, and the in-process buffers are refilled for T.
- static/dashboard.html runs unchanged in headless Chromium. Its /dashboard/state requests are
  answered from that replay.
- Playwright's fake clock drives the page's timers (poll, countdown, spinner), so each frame is
  a pure function of T. Reduced motion is on, so no wall-clock CSS animation.

Only time is compressed, by SPEED. The demo buttons are hidden (demo_mode false).

Output: video/work/dash/<segment>/NNNNN.jpg (1920x1080 at 2x = 3840x2160) plus index.json
({frame: replay time}), then video/footage/dashboard_full.mp4 (both segments, 30 fps).
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from app import dashboard  # noqa: E402
from app.drives import Drive  # noqa: E402
from app.jobs import Job  # noqa: E402

VIDEO = ROOT / "video"
CACHE = VIDEO / "work" / "replay_drive8.json"
OUT = VIDEO / "work" / "dash"
FPS = 30
SPEED = 6.0  # replay seconds per output second
VIEWPORT = {"width": 1920, "height": 1080}
ET = timedelta(hours=-4)  # EDT; all clock strings below are local wall time on 2026-10-03


def at(hms: str) -> datetime:
    h, m, s = hms.split(":")
    local = datetime(2026, 10, 3, int(h), int(m), 0, tzinfo=UTC) + timedelta(seconds=float(s))
    return local - ET


# Segments of the drive to capture: (name, start, end).
SEGMENTS = [
    ("departure", at("22:24:58"), at("22:28:22")),  # plug-in → call → dispatch → PR #11 merged
    ("arrival", at("22:31:50"), at("22:32:26")),  # arrival countdown → arrival call rings
]

# ToolCallRecorder rows of drive #8, as the live TOOLS window showed them.
TOOLS = [
    ("22:25:16", "set_destination", "the Landmark", 2581),
    ("22:25:30", "search_web", "ramen places nearby", 3073),
    ("22:25:48", "search_web", "Asian restaurants nearby", 3104),
    ("22:26:45", "dispatch_task", "coder: Fix bug with email capitalization", 165),
    (
        "22:27:02",
        "draft_message",
        "Daniel: Send an email saying that I completed my work and I'm excited to see him"
        " for the day",
        669,
    ),
    (
        "22:27:12",
        "dispatch_task",
        "email: I'm done with my work and I'm really excited to see you today!",
        152,
    ),
]
DEPARTURE_CONNECTED = at("22:25:09")  # ElevenLabs start_time_unix_secs 1791080709
DEPARTURE_ENDED = at("22:27:31")  # LOG "call ended after 02:19"
DEPARTURE_SECONDS = 139
ARRIVAL_CONNECTED = at("22:32:18")  # ElevenLabs start_time_unix_secs 1791081138
SERVER_STARTED = at("21:54:01")  # UP 32m50s at 22:26:51
# Probe readings on the live recording (ms), alternated per 30 s health cycle.
HEALTH_READINGS = [
    {"neon": 21, "elevenlabs": 59, "twilio": 55, "anthropic": 120, "github": 207, "ntfy": 25},
    {"neon": 20, "elevenlabs": 56, "twilio": 35, "anthropic": 130, "github": 220, "ntfy": 23},
]
API_MS = [105, 117]
HEALTH_PHASE = at("22:26:44")
UNPROBED = {"google_routes": "not probed: billed per request"}


def load() -> dict:
    data = json.loads(CACHE.read_text())

    def parse(row: dict) -> dict:
        out = dict(row)
        for k, v in row.items():
            if isinstance(v, str) and k.endswith("_at") or k in ("eta", "at"):
                out[k] = datetime.fromisoformat(v) if isinstance(v, str) else v
        return out

    return {
        "drive": parse(data["drive"][0]),
        "calls": [parse(c) for c in data["calls"]],
        "jobs": [parse(j) for j in data["jobs"]],
        "events": [parse(e) for e in data["events"]],
    }


class Replay:
    def __init__(self, data: dict):
        self.d = data
        self.set_destination_done = at(TOOLS[0][0]) + timedelta(milliseconds=TOOLS[0][3])

    # ── rows as they stood at T ────────────────────────────────────────────
    def job_as_of(self, row: dict, T: datetime) -> tuple[Job, list[dict]] | None:
        if row["created_at"] > T:
            return None
        hist = [
            {k: e[k] for k in ("job_id", "from_state", "to_state", "note", "at")}
            for e in self.d["events"]
            if e["job_id"] == row["id"] and e["at"] <= T
        ]
        state = hist[-1]["to_state"]
        final = dict(row["result"] or {})
        result: dict = {}
        running = next((e["at"] for e in hist if e["to_state"] == "running"), None)
        approved = next((e["at"] for e in hist if e["to_state"] == "approved"), None)
        if row["type"] == "coder":
            if running:
                result.update(issue_number=final["issue_number"], issue_url=final["issue_url"])
            pr_at = datetime.fromisoformat(final["pr_opened_at"])
            if pr_at <= T:
                result.update(
                    pr_number=final["pr_number"],
                    pr_url=final["pr_url"],
                    pr_opened_at=final["pr_opened_at"],
                    head_sha=final["head_sha"],
                    tests="success" if approved else "pending",
                )
        elif approved:
            result = final
        job = Job(
            **{
                **row,
                "state": state,
                "result": result or None,
                "summary": row["summary"] if state in ("done", "failed") else None,
                "updated_at": hist[-1]["at"],
            }
        )
        return job, hist

    async def read(self, _conn, T: datetime):
        drive = dict(self.d["drive"])
        drive["ended_at"] = None
        if T < self.set_destination_done:
            drive.update(destination=None, eta=None, arrival_call_at=None)
        calls = [
            {k: c[k] for k in ("kind", "conversation_id", "job_ids", "at")}
            for c in self.d["calls"]
            if c["at"] <= T
        ]
        drive["arrival_called"] = any(c["kind"] == "arrival" for c in calls)
        jobs, history = [], {}
        for row in self.d["jobs"]:
            got = self.job_as_of(row, T)
            if got:
                jobs.append(got[0])
                history[row["id"]] = got[1]
        return Drive(**drive), calls, jobs, history, None

    # ── in-process buffers at T ────────────────────────────────────────────
    def buffers(self, T: datetime) -> None:
        dashboard.TOOL_CALLS.clear()
        dashboard.TOOL_EVENTS.clear()
        events = []
        for hms, tool, query, ms in TOOLS:
            start = at(hms)
            if start + timedelta(milliseconds=ms) <= T:
                dashboard.TOOL_CALLS.appendleft(
                    {"at": start, "tool": tool, "query": query, "latency_ms": ms, "ok": True}
                )
                events.append((start, "tool_called", f'{tool} "{query}" {ms}ms'))
        if T >= DEPARTURE_CONNECTED:
            events.append(
                (DEPARTURE_CONNECTED, "call_started", "call connected: agent on the line")
            )
        if T >= DEPARTURE_ENDED:
            m, s = divmod(DEPARTURE_SECONDS, 60)
            events.append((DEPARTURE_ENDED, "call_ended", f"call ended after {m:02d}:{s:02d}"))
        if T >= ARRIVAL_CONNECTED:
            events.append((ARRIVAL_CONNECTED, "call_started", "call connected: agent on the line"))
        for when, type_, message in sorted(events):
            dashboard.add_event(type_, message, at=when)

        calls = {c["kind"]: c["conversation_id"] for c in self.d["calls"]}
        dashboard.LIVE_CALL.clear()
        if T >= ARRIVAL_CONNECTED:
            dashboard.LIVE_CALL.update(
                conversation_id=calls["arrival"],
                status="active",
                started_at=ARRIVAL_CONNECTED,
                ended_at=None,
                duration_s=None,
            )
        elif T >= DEPARTURE_ENDED:
            dashboard.LIVE_CALL.update(
                conversation_id=calls["departure"],
                status="ended",
                started_at=DEPARTURE_CONNECTED,
                ended_at=DEPARTURE_ENDED,
                duration_s=DEPARTURE_SECONDS,
            )
        elif T >= DEPARTURE_CONNECTED:
            dashboard.LIVE_CALL.update(
                conversation_id=calls["departure"],
                status="active",
                started_at=DEPARTURE_CONNECTED,
                ended_at=None,
                duration_s=None,
            )

        cycle = int((T - HEALTH_PHASE).total_seconds() // dashboard.HEALTH_SECONDS)
        checked = HEALTH_PHASE + timedelta(seconds=cycle * dashboard.HEALTH_SECONDS)
        readings = HEALTH_READINGS[cycle % 2]
        dashboard.HEALTH.clear()
        for name in dashboard.SERVICES:
            if name == "api_server":
                continue
            unprobed = UNPROBED.get(name)
            dashboard.HEALTH[name] = {
                "name": name,
                "ok": None if unprobed else True,
                "latency_ms": None if unprobed else readings[name],
                "checked_at": checked,
                "detail": unprobed,
            }
        self.api_ms = API_MS[cycle % 2]

    async def state(self, T: datetime) -> dict:
        self.buffers(T)
        dashboard._read = lambda conn, now: self.read(conn, now)
        s = await dashboard.build_state(now=T)
        s["server"]["uptime_s"] = int((T - SERVER_STARTED).total_seconds())
        s["server"]["demo_mode"] = False
        s["server"]["build"] = "replay"
        for svc in s["services"]:
            if svc["name"] == "api_server":
                svc["latency_ms"] = self.api_ms
        return s


class FakePool:
    @contextlib.asynccontextmanager
    async def connection(self, timeout=None):
        yield None


async def capture(replay: Replay, segments, probe: datetime | None = None) -> None:
    from playwright.async_api import async_playwright

    dashboard.db.get_pool = lambda: FakePool()
    # The page's AGENTS window finds workers by task name (app/main.py); give it the same names.
    keep = [
        asyncio.create_task(asyncio.Event().wait(), name=n)
        for n in ("planner", "coder", "research", "email", "calls", "dashboard")
    ]
    page_html = (ROOT / "static" / "dashboard.html").read_text()
    clock = {"T": segments[0][1]}

    async with async_playwright() as p:
        browser = await p.chromium.launch()
        ctx = await browser.new_context(
            viewport=VIEWPORT, device_scale_factor=2, reduced_motion="reduce"
        )
        page = await ctx.new_page()

        async def on_route(route):
            url = route.request.url
            if "/dashboard/state" in url:
                body = json.dumps(await replay.state(clock["T"]))
                await route.fulfill(status=200, content_type="application/json", body=body)
            elif url.split("#")[0].endswith("/dashboard"):
                await route.fulfill(status=200, content_type="text/html", body=page_html)
            else:
                await route.continue_()

        await page.route("http://replay.local/**", on_route)
        for name, start, end in segments:
            if probe:
                start, end = probe, probe
            clock["T"] = start - timedelta(seconds=3)
            await page.clock.install(time=clock["T"])
            await page.goto("http://replay.local/dashboard#token=replay")
            await page.wait_for_load_state("networkidle")
            await page.evaluate("document.fonts.ready")
            # settle: two polls before the first frame
            for _ in range(30):
                clock["T"] += timedelta(milliseconds=100)
                await page.clock.run_for(100)
                await asyncio.sleep(0.01)
            n = int((end - start).total_seconds() * FPS / SPEED) + 1
            folder = OUT / ("probe" if probe else name)
            folder.mkdir(parents=True, exist_ok=True)
            index = {}
            step = 1000 * SPEED / FPS
            t0 = time.time()
            for i in range(n):
                target = start + timedelta(milliseconds=i * step)
                ms = round((target - clock["T"]).total_seconds() * 1000)
                clock["T"] = target
                if ms > 0:
                    await page.clock.run_for(ms)
                await asyncio.sleep(0.04)  # a fulfilled poll resolves in real time
                await page.screenshot(path=str(folder / f"{i:05d}.jpg"), type="jpeg", quality=92)
                index[i] = (target + ET).strftime("%H:%M:%S.%f")[:-3]
                if i % 100 == 0:
                    print(f"{name} {i}/{n} {index[i]} ({time.time() - t0:.0f}s)", flush=True)
            (folder / "index.json").write_text(json.dumps(index, indent=0))
            if probe:
                break
        await browser.close()
    for task in keep:
        task.cancel()


def encode() -> None:
    lists = []
    for name, *_ in SEGMENTS:
        lists.append(f"{OUT / name}/%05d.jpg")
    parts = []
    for i, pattern in enumerate(lists):
        part = OUT / f"part{i}.mp4"
        subprocess.run(
            ["ffmpeg", "-v", "error", "-y", "-framerate", str(FPS), "-i", pattern,
             "-vf", "scale=1920:1080:flags=lanczos", "-c:v", "libx264", "-crf", "16",
             "-pix_fmt", "yuv420p", str(part)],
            check=True,
        )  # fmt: skip
        parts.append(part)
    concat = OUT / "parts.txt"
    concat.write_text("".join(f"file '{p}'\n" for p in parts))
    dest = VIDEO / "footage" / "dashboard_full.mp4"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(concat),
         "-c", "copy", str(dest)],
        check=True,
    )  # fmt: skip
    print("wrote", dest)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--probe", help="one frame at HH:MM:SS (local), into work/dash/probe")
    ap.add_argument("--no-encode", action="store_true")
    args = ap.parse_args()
    replay = Replay(load())
    probe = at(args.probe) if args.probe else None
    asyncio.run(capture(replay, SEGMENTS, probe))
    if not probe and not args.no_encode:
        encode()
    return 0


if __name__ == "__main__":
    sys.exit(main())
