# Demo video: study notes (step 1)

Written 2026-10-03, 23:40 EDT, before any build. Media lives in `video/reference`, `video/footage` and `video/work`, all git-ignored.

## Length rule
- https://mhacks-2026.devpost.com/rules sets no video length or hosting rule. The only requirement is access to the code.
- The 3–5 min rule in DECISIONS §6 belonged to Fetch.ai, which was cut (D17).
- Target: 85–100 s.

## Reference: teaser_v3d.mp4
- 960x540, 24 fps, 29.8 s.
- **Cuts (scene > 0.12):** 0.42, 3.33, 5.83, 8.33, 9.58, 12.08, 15.83, 16.67, 18.33, 23.33. A strobe runs from 23.3 to 25.0 s (about 10 cuts, 2–5 frames each). The end card holds from 25.0 to 29.8 s.
- **Shots before the strobe:** average 2.5 s. Most are exactly 2.5 s, some 1.25 s. Spectral-flux autocorrelation gives a 0.418 s pulse (144 BPM, or 96 BPM with triplets), so a cut lands every 6 pulses.
- **Grammar to copy:**
  - Headlines are heavy caps, top-left, one word per beat, and accumulate over 2 lines.
  - Each headline has one stylized accent word ("TALK", "YOU").
  - Callouts are tiny mono labels on thin leader lines with a dot on the object.
  - A typed mono task list sits bottom-left, with its lines deleting.
  - Shots hold on the product with slow pushes.
  - A strobe of bars comes before the logo end card, which carries a tagline.
- Loudness is flat (about −12 to −13 dB RMS) all the way through.

## Footage

| File | What | Use |
|---|---|---|
| footage/self_start.mov (= Desktop IMG_3918) | Daniel on the hood, night, 1080p30 HEVC, 12.7 s | **intro**: in 2.6 s, out 12.0 s (the first word starts at 2.76 s, the last ends at about 11.7 s) |
| footage/carplay_on.mov (= IMG_3916) | Dash, night, 1080p30, 19.4 s | **plug-in**: in 3.0 s, out 8.6 s. "No Device Connected" switches to CarPlay at **6.0 s** |
| | | **ring**: in 10.4 s, out 18.8 s. The incoming "Shotgun" call appears at **10.8 s**, Accept is tapped at about 14.4 s, the call timer starts at 14.8 s, and "Shotgun here. / Where are we headed?" plays at 16.3–18.5 s |
| ~/Movies/shotgun/2026-10-03_222241/dashboard.mp4 | Live dashboard, 1440x900, 25 fps, 50 min, real drive #8 | backup dashboard footage |
| …/dashboard_sharp.mp4 + frames/ | 2880x1800 stills about every 1.2 s | crisp crops |
| ElevenLabs conv_5101… (d8_departure.mp3, 139 s) | Real departure call, drive #8, both sides | **talk** audio |
| ElevenLabs conv_8601… (d8_arrival.mp3, 65 s) | Real arrival call, drive #8 | **arrival** audio |

**Problems:**
- carplay_on.mov has engine noise from 0 to 3 s. Whisper heard "music" from 11.6 to 14.3 s, which is the ringtone through the car speakers.
- The phone camera exposure breathes slightly.
- No cable is in frame, so the "plug in" callout has to point at the screen.
- self_start.mov is steady and usable throughout.
- Both .mov files carry GPS metadata. The re-encode drops it, and the files are never committed.

**Intro transcript** (whisper medium.en; the product names need Daniel to confirm):
> Everyone's building agents for your phone, Muse, Instinct. And if you watch the OpenAI Dev Day drop, dots, but we built one for your car.

## Real numbers (drive #8, 2026-10-03)

**Recording clock:** the recording starts at 22:22:41. The dashboard frame at t = 352 s shows SERVER 22:28:33.

| Time | Rec t (s) | Event | Source |
|---|---|---|---|
| 22:25:01 | 140 | CarPlay connected, drive #8 opened, departure call placed | Neon drives/calls, dashboard LOG |
| 22:25:09 | 148 | call connected | ElevenLabs start_time, LOG |
| 22:25:16 | 155 | set_destination "the Landmark" **2581 ms** → "About 10 minutes" | dashboard TOOLS (ToolCallRecorder) |
| 22:25:30 | 169 | search_web "ramen places nearby" **3073 ms** | TOOLS |
| 22:25:48 | 187 | search_web "Asian restaurants nearby" **3104 ms** | TOOLS |
| 22:26:45 | 244 | dispatch_task coder **165 ms**, preapproval merge-if-tests-pass, require_tests_pass | TOOLS, jobs #189 |
| 22:26:46 | 245 | #189 issue **#10** filed, @claude tagged | job_events |
| 22:27:02 | 261 | draft_message **669 ms** | TOOLS |
| 22:27:12 | 271 | dispatch_task email **152 ms** (#190) | TOOLS |
| 22:27:14 | 273 | #190 sent (pre-approved) | job_events |
| 22:27:31 | 290 | departure call ended after **2:19**, 6 tools | LOG |
| 22:27:51 | 310 | **PR #11** opened, waiting for tests | LOG |
| 22:28:05 | 324 | tests passed, pre-approval met → approved | job_events |
| 22:28:09 | 328 | merged. Dispatch to merge took **1:24** | job_events, JOBS 01:24 |
| 22:32:07 | — | arrival_call_at = ETA 22:35:07 − 3 min | drives |
| 22:32:08 | 567 | arrival call placed | calls |
| 22:48:12 | 1531 | drive #8 closed by the next plug-in (not an unplug, so **no recap push** in this recording) | drives |

**Other measured values:**
- **Plug-in → ring:** 4.8 s in the footage (CarPlay on at 6.0 s, incoming call at 10.8 s). Server side (drive #12, the filmed one): the Shortcut event and the call both at 22:59:36, answered 22:59:42.
- **Health probe:** every 30 s (`HEALTH_SECONDS = 30.0`, app/dashboard.py:62).
- **ElevenLabs round-trip tool latencies** (they include network): search_web 3.17 s, dispatch_task 275 ms.

**Not available:**
- **/events latency:** neither Railway logs nor the app record it, so its callout shows the label only.
- **"An hour a day" driving:** no source in docs/, so the copy reads "HOURS A WEEK".

## Missing footage
- **talk.mp4:** no video of the driver talking. The real call audio (drive #8) exists.
- **arrival.mp4:** audio only.
- **recap.png:** no ntfy screenshot, and drive #8 had no unplug.
- **The GitHub PR page.**
- **A 1080p dashboard capture:** the existing one is 1440x900. Its live data is real but in real time.
