---
name: shotgun-dashboard-design
description: Visual design rules for the Shotgun demo dashboard (/dashboard). Use whenever building or changing the dashboard's HTML, CSS or JS. Terminal / tiling-window-manager aesthetic, dark techy palette, multiple small live "windows".
---

# Shotgun dashboard design

The dashboard is "mission control" for a voice agent riding in a car. Judges watch it
from about 3 feet away on a laptop while the agent works. It should look like a
developer's tiled terminal session (tmux / i3 / a trading terminal), not a SaaS admin
panel. Every pixel should feel deliberate.

## The look

- **Tiling layout.** The whole viewport is a grid of rectangular windows that fill
  the screen edge to edge with a 6px gap. There is no page scroll, no hero header, and
  no centered container. Individual windows scroll internally.
- **Windows.**
  - Each window has a 1px border and a one-line title bar:
    `┌─ JOBS ──────────── 3 running ─┐` style.
  - Left side of the title bar: an uppercase label. Right side: a small live stat.
  - Use square corners (border-radius 0 or 2px max), with no drop shadows.
- **Type.**
  - Monospace everywhere: JetBrains Mono (Google Fonts), falling back to
    ui-monospace.
  - Sizes: 13px body, 11px for labels/meta, and 28-40px only for the single hero
    number (the countdown).
  - Use `font-variant-numeric: tabular-nums` so numbers don't jitter.
- **Palette.** Define these as CSS variables on :root. Dark only.
  - Background: `--bg: #07090c`. Window background: `--panel: #0c1015`.
    Borders: `--line: #1c2530`.
  - Text: `--fg: #c9d4df`. Dim text: `--dim: #5c6b7a`.
  - One accent, used sparingly for "alive" things (the cursor, active borders, the
    countdown): phosphor green `--accent: #39ff9c`.
  - Status colors:
    - running: `--amber: #ffb347`
    - done: `--ok: #39ff9c`
    - exception: `--err: #ff5f6d`
    - queued: `--dim`
    - on call: `--call: #5fd4ff`
- **Motion.** Keep it subtle and purposeful.
  - New log lines slide in or fade in over 150ms.
  - The active window's border pulses to the accent color when something
    happens in it.
  - A blinking block cursor `█` sits at the end of the log.
  - The countdown ticks every second.
  - Nothing bounces, and there are no spinners. Use an ASCII spinner
    `⠋⠙⠹⠸⠼⠴⠦⠧` instead.
- **Data as text.**
  - Progress is an ASCII bar: `[████████░░░░] 67%`.
  - States are bracketed tags: `[RUNNING]`, `[DONE]`, `[EXCEPTION]`.
  - Timestamps are `HH:MM:SS`.
  - Latency shows in ms: `search_web 3712ms`.

## Windows (suggested grid, desktop 1440x900)

| Window | Contents |
|---|---|
| **DRIVE** (top-left, large) | State (PLUGGED IN / ON CALL / SILENT / PARKED), destination, ETA, big countdown to the arrival call |
| **CALL** | Live call status, duration timer, last tool used; optional live transcript lines if available |
| **LOG** (tall, right side) | `tail -f` style event timeline, newest at the bottom, auto-scrolls, blinking cursor |
| **JOBS** | One row per job: id, type, state tag, ASCII progress, pre-approval condition, PR link |
| **TOOLS** | Recent inline tool calls with query and latency, so judges see "it searched live" |
| **AGENTS** | Small ASCII diagram of voice agent → orchestrator → workers, with the active node highlighted |
| **SYSTEM** (thin strip at the bottom) | Server uptime, DB ok, ElevenLabs ok, demo-mode buttons styled as `[ SIMULATE PLUG-IN ]` text buttons |

## Do not (this is the "vibe-coded dashboard" look to avoid)

- Inter/Roboto/system sans, or rounded cards with big shadows
- Purple/blue gradients, glassmorphism, neon everywhere
- Emoji icons, icon libraries, or chart libraries for things text can show
- A Tailwind-default look, pastel badges, or a "Welcome back" header
- Light mode, or more than one accent color doing decoration
- Lorem ipsum or fake numbers. Every value comes from /dashboard/state; empty
  states read like a terminal: `-- no jobs this drive --`

## Build constraints

- One self-contained HTML file: inline CSS and JS, Google Fonts only, and no
  frameworks or build step.
- Poll /dashboard/state every 1.5s. Diff the new state against the old one so
  only changed rows animate.
- Check the layout at 1440x900 and 1280x800. Below 900px wide, stack the windows
  vertically (phones are a secondary concern).
- Before finishing, screenshot the page (headless browser) with a mocked 2-job
  drive and check it against this file.
