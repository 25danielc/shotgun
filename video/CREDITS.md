# Credits

## Music and sound effects

All music and sound effects in this video are original. They were synthesized in code for this video by `video/tools/music.py` (oscillators, filtered noise and a synthetic reverb, with fixed random seeds so every render is identical). No samples, loops, presets or third-party recordings were used. The music bed and the SFX are free to use in this video on YouTube and Devpost, and anywhere else.

## Footage, audio and data

- **Intro and car footage:** Daniel Chen, filmed 2026-10-03 on an iPhone (`self start.MOV`, `carplay on.MOV`). GPS metadata is dropped in the re-encode, and the source files are never committed.
- **Call audio:** the real Shotgun calls of drive #8 on 2026-10-03, from ElevenLabs:
  - departure call `conv_5101…` (22:25)
  - arrival call `conv_8601…` (22:32)
  - The CarPlay clip's ring and greeting are the real call of drive #12.
- **The ringtone** in the CarPlay clip is the iPhone's own, heard incidentally through the car speakers.
- **Dashboard:** `static/dashboard.html`, replayed frame-exact from drive #8's Neon rows by `tools/replay_dashboard.py`. Nothing is mocked; only time is compressed.
- **Drive recap text:** `app/recap.py` over drive #8's jobs.

## Type and brand

- **JetBrains Mono** (Regular, Medium, ExtraBold), © The JetBrains Mono Project Authors, SIL Open Font License 1.1. See `comp/fonts/OFL.txt`.
- **Lit Seat mark:** `static/brand/shotgun-mark.svg` (Shotgun).
