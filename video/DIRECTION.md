# Shotgun demo video: direction

**Message.** This video tells hackathon judges, most of them watching muted, that Shotgun is an AI passenger: one voice agent that owns subagents. Plug in and it calls you. You hand off work by voice, subagents do it while you drive, and one call comes before you park. The video shows it working in a real car.

**Deliverable.**
- 1920x1080 at 30 fps, 99.8 s.
- For Devpost and YouTube.
- The MHacks rules page sets no length limit.

**Concept.** The dashboard is mission control, and the video lives inside it: the same palette, mono type and box-drawing frames. Real footage and the real replayed dashboard carry the proof. Type carries the story for muted viewers.

**Recurring idea.** The blinking █ cursor and one phosphor-green word per headline mark what's alive.

**Palette** (from the shotgun-dashboard-design skill):

| Role | Color |
|---|---|
| bg | #07090c |
| panel | #0c1015 |
| line | #1c2530 |
| fg | #c9d4df |
| dim | #5c6b7a |
| accent | #39ff9c |

- **Where the accent appears:** the accent word in each headline, the █ cursor, the mark's passenger seat, tags (`[DONE]`, the pre-approval tag, the PR overlay), the "SHOTGUN:" caption prefix, the scanlines and the diagram's data packets.
- **Status colors** (amber, call blue) appear only inside the dashboard footage.

**Type.** JetBrains Mono only (OFL, files in `comp/fonts`):
- **Headlines:** 800, uppercase, −0.01em, 100–108 px.
- **Callouts:** 500, uppercase, 0.08em, 30 px. The brief asked for 22–26 px, but 30 px is the smallest size that survives the 640x360 readability test.
- **Captions:** 500, 34 px, sentence case, on a #07090c box at 80%.

**Camera language.**
- Locked shots with slow pushes (push_in 1.00→1.08, IO).
- Punches into dashboard windows (420 ms IO, then push_in).
- Hard cuts on the beat inside the dashboard section.
- Exactly one swipe (out of the intro) and one scan wipe (in the problem shot).

**Texture.** Flat, plus film grain at 0.04 opacity, re-seeded every frame from six seeded tiles.

**Motion vocabulary.** The presets are named in `comp/engine.js`: word_in, word_out, callout, type_line, push_in, punch, swipe_down, scan_wipe, tag_done, mark_build. All of them are deterministic functions of t.

**Banned.** Gradients, glassmorphism, emoji, stock icons, recreated CarPlay/iOS UI (we show the real screen), shockwaves, particles, RGB split, shake, lens flares, bouncy easing, and invented numbers.

**Real assets.**
- **Footage:** `self start.MOV` (intro) and `carplay on.MOV` (plug-in and ring).
- **Call audio:** the drive #8 departure and arrival calls from ElevenLabs.
- **Dashboard:** a frame-exact replay of drive #8 from its Neon rows (`tools/replay_dashboard.py`).
- **Recap:** `recap_text` over drive #8's jobs.
- **Logo:** the Lit Seat mark (`static/brand`).
- **Abstract call card:** used for the talk and arrival calls, because no in-car video exists of those calls.
