"""One-off: write the first video/timeline.json from the beat plan (beats -> T seconds).
After this, timeline.json is the source of truth; edit it, not this script."""

import json
from pathlib import Path

B = 60 / 105


def T(k):
    return round(0.4 + B * k, 3)


def words(spec):  # [(text, beat[, accent])]
    out = []
    for item in spec:
        w, k = item[0], item[1]
        out.append(
            {"text": w, "t": T(k), **({"accent": True} if len(item) > 2 and item[2] else {})}
        )
    return out


tl = {
    "_doc": (
        "All times are T seconds after the intro clip ends (global = intro length + T), e"
        "xcept intro.* (seconds into the intro clip) and *_src (seconds into the source f"
        "ile). snap=true moves every T to the nearest half-beat of the music grid (bpm, d"
        "ownbeat_T) at build time; tools/build.py reports moves > 0.25 s."
    ),
    "fps": 30,
    "width": 1920,
    "height": 1080,
    "snap": True,
    "music": {
        "bpm": 105,
        "downbeat_T": 0.4,
        "file": "work/audio/music.wav",
        "duck_speech_db": -10,
        "duck_ring_db": -8,
        "fade_out_end_T": 90.4,
    },
    "end_T": 90.4,
    "intro": {
        "src": "footage/self_start.mov",
        "in": 2.6,
        "out": 12.0,
        "lower_third": {"text": "DANIEL CHEN · BUILDER", "t": 1.0, "exit": 4.0},
        "captions": [
            {
                "start": 0.06,
                "end": 3.82,
                "text": "Everyone's building agents for your phone,\nMuse, Instinct.",
            },
            {
                "start": 3.82,
                "end": 6.95,
                "text": "And if you watch the OpenAI Dev Day drop,\ndots,",
            },
            {"start": 6.95, "end": 9.4, "text": "but we built one for your car."},
        ],
    },
    "A_swipe": {"start": 0.0, "end": T(0)},
    "B_thesis": {
        "start": T(0),
        "end": T(8),
        "mark_size": 240,
        "mark_slide": T(2.5),
        "mark_small": 96,
        "headline": words(
            [("YOUR", 3), ("CAR", 3.5), ("HAS A", 4), ("PASSENGER", 5, True), ("NOW", 6)]
        ),
        "headline_breaks_after": ["CAR"],
        "exit": T(7.5),
    },
    "C_problem": {
        "start": T(8),
        "end": T(16),
        "headline1": words([("YOU DRIVE", 8.5), ("HOURS", 9.5, True), ("A WEEK", 10)]),
        "headline1_breaks_after": ["YOU DRIVE"],
        "callout1": {"text": "time you can't spend on a screen", "t": T(11)},
        "wipe": T(12.5),
        "headline2": words([("YOUR AI", 13), ("CAN'T", 13.5), ("COME", 14, True)]),
        "headline2_breaks_after": ["YOUR AI"],
        "exit": T(15.5),
    },
    "D_plugin": {
        "start": T(16),
        "end": T(24),
        "src": "footage/carplay_on.mov",
        "src_in": 4.572,
        "headline": words([("PLUG", 17), ("IN.", 17.5, True)]),
        "callout": {
            "text": "CARPLAY_CONNECTED → /EVENTS",
            "t": T(18.5),
            "target": [900, 460],
            "label_off": [300, 380],
        },
    },
    "E_ring": {
        "start": T(24),
        "end": T(39),
        "src": "footage/carplay_on.mov",
        "src_in": 10.229,
        "headline": words([("IT", 26), ("CALLS", 26.5, True), ("YOU", 27)]),
        "headline_breaks_after": ["CALLS"],
        "exit": T(38),
        "callouts": [
            {
                "text": "ELEVENLABS AGENT · TWILIO OUTBOUND",
                "t": T(28),
                "target": [940, 455],
                "label_off": [140, -330],
                "exit": T(38),
            },
            {
                "text": "CARPLAY ON → RING 4.8 S",
                "t": T(30.5),
                "target": [1195, 605],
                "label_off": [80, 230],
                "exit": T(38),
            },
        ],
        "captions": [
            {
                "start_src": 16.2,
                "end_src": 18.6,
                "speaker": "SHOTGUN",
                "text": "Shotgun here. Where are we headed?",
            }
        ],
    },
    "F_talk": {
        "start": T(39),
        "end": T(76),
        "audio_src": "work/audio/d8_departure.wav",
        "call_started_src": 0.0,
        "headline": words([("TALK.", 39.5), ("IT", 40, True), ("HANDLES", 40.5), ("IT", 41)]),
        "headline_breaks_after": ["IT"],
        "exit": T(75),
        "clips": [
            {
                "id": "c1",
                "t": T(40),
                "in": 17.33,
                "out": 19.62,
                "speaker": "DANIEL",
                "captions": [{"text": "Can you find me some ramen places nearby?"}],
            },
            {
                "id": "c2",
                "t": T(45.5),
                "in": 25.05,
                "out": 27.80,
                "speaker": "SHOTGUN",
                "captions": [{"text": "You've got Tomukun Noodle Bar\nand Slurping Turtle."}],
            },
            {
                "id": "c3",
                "t": T(51.5),
                "in": 64.45,
                "out": 72.20,
                "speaker": "DANIEL",
                "captions": [
                    {
                        "text": "Before I left, I remember I had a bug\nin my demo workspace.",
                        "until_src": 68.85,
                    },
                    {"text": "It has to do with email capitalization.", "until_src": 70.7},
                    {"text": "Do you think you could go in\nand fix that for me?"},
                ],
            },
            {
                "id": "c4",
                "t": T(66),
                "in": 90.36,
                "out": 92.30,
                "speaker": "SHOTGUN",
                "captions": [{"text": "Want me to merge it if the tests pass?"}],
            },
            {
                "id": "c5",
                "t": T(70),
                "in": 93.10,
                "out": 94.25,
                "speaker": "DANIEL",
                "captions": [{"text": "Uh, yeah, that'd be great."}],
            },
        ],
        "panel_lines": [
            {"t": T(40), "who": "you", "text": "find me some ramen places nearby"},
            {"t": T(45), "kind": "chip", "text": "search_web · 3073 ms", "done": True},
            {"t": T(60.5), "who": "you", "text": "fix the email capitalization bug"},
            {"t": T(66), "who": "shotgun", "text": "merge it if the tests pass?"},
            {"t": T(70), "who": "you", "text": "yeah, that'd be great"},
            {"t": T(72), "kind": "tag", "text": "preapproval: require_tests_pass"},
            {
                "t": T(73),
                "kind": "chip",
                "text": "dispatch_task → coder subagent · 165 ms",
                "done": True,
            },
        ],
    },
    "G_subagents": {
        "start": T(76),
        "end": T(100),
        "headline": words(
            [("SUBAGENTS", 76.5, True), ("WORK", 77), ("WHILE", 77.5), ("YOU", 78), ("DRIVE", 78.5)]
        ),
        "headline_breaks_after": ["WORK"],
        "exit": T(79.5),
        "headline_bg_replay": "22:26:47",
        "windows": [
            {
                "id": "jobs",
                "t": T(80),
                "rect": [505, 12, 777, 300],
                "replay": [
                    [0, "22:26:44"],
                    [0.5, "22:26:47"],
                    [1.5, "22:27:52"],
                    [2.5, "22:28:06"],
                    [3.5, "22:28:10"],
                    [4, "22:28:11"],
                ],
                "done_ticks_beats": [0.5, 1.5, 1.5, 2.5, 2.5, 3.5],
                "callouts": [
                    {
                        "text": "STEP-LEVEL STATE · NEON POSTGRES",
                        "t_beat": 0.75,
                        "target": [538, 94],
                        "label_off": [60, -200],
                    }
                ],
            },
            {
                "id": "agents",
                "t": T(84),
                "rect": [6, 832, 493, 212],
                "replay": [[0, "22:26:40"], [1, "22:26:46"], [4, "22:26:52"]],
                "callouts": [
                    {
                        "text": "1 VOICE AGENT → 3 SUBAGENTS",
                        "t_beat": 1.0,
                        "target": [38, 859],
                        "label_off": [80, -260],
                    },
                    {
                        "text": "CLAUDE CODE VIA GITHUB ACTIONS",
                        "t_beat": 2.0,
                        "target": [113, 900],
                        "label_off": [420, 300],
                    },
                ],
            },
            {
                "id": "tools",
                "t": T(88),
                "rect": [505, 832, 777, 212],
                "replay": [[0, "22:25:14"], [4, "22:27:16"]],
                "callouts": [
                    {
                        "text": "INLINE TOOLS < 8 S · BACKGROUND < 500 MS",
                        "t_beat": 1.0,
                        "target": [760, 860],
                        "label_off": [-40, -250],
                    }
                ],
            },
            {
                "id": "log",
                "t": T(92),
                "rect": [1288, 12, 626, 520],
                "replay": [[0, "22:26:40"], [4, "22:28:12"]],
                "push_in": True,
            },
            {
                "id": "services",
                "t": T(96),
                "rect": [1288, 832, 626, 212],
                "replay": [[0, "22:28:15"], [4, "22:28:17"]],
                "callouts": [
                    {
                        "text": "HEALTH-CHECKED EVERY 30 S",
                        "t_beat": 1.0,
                        "target": [1437, 860],
                        "label_off": [80, -250],
                    }
                ],
            },
        ],
    },
    "H_arrival": {
        "start": T(100),
        "end": T(114),
        "countdown": {
            "t": T(100),
            "until": T(103.5),
            "rect": [6, 12, 493, 210],
            "replay": [[0, "22:32:02"], [3.0, "22:32:08"], [3.5, "22:32:09"]],
            "callout": {
                "text": "ETA − 3 MIN · GOOGLE ROUTES",
                "t_beat": 0.5,
                "target": [77, 154],
                "label_off": [520, 200],
            },
        },
        "headline1": words([("ONE CALL", 101), ("TO HAND", 101.5), ("IT OFF", 102)]),
        "headline2": words(
            [("ONE CALL", 104), ("BEFORE", 104.5), ("YOU", 105), ("PARK", 105.5, True)]
        ),
        "exit1": T(103.5),
        "exit2": T(113.5),
        "audio": {
            "t": T(103.5),
            "src": "work/audio/d8_arrival.wav",
            "in": 0.35,
            "out": 6.30,
            "captions": [
                {"text": "Shotgun here.\nYou're about three minutes out.", "until_src": 2.85},
                {"text": "Got the email capitalization fix merged\nand tests came through."},
            ],
        },
        "overlay": {"text": "PR #11 MERGED · TESTS PASSED", "t": T(111.5)},
    },
    "I_parked": {
        "start": T(114),
        "end": T(121),
        "recap_title": "Shotgun: drive recap",
        "recap_lines": [
            "Done: I merged the fix for the email capitalization bug, and the tests passed.",
            "Done: I sent your email to Daniel.",
        ],
        "headline": words([("PARK.", 114.5), ("READ", 115, True), ("LATER.", 115.5)]),
        "callout": {"text": "UNPLUG → NTFY RECAP. NO CALL.", "t": T(116.5)},
    },
    "J_how": {
        "start": T(121),
        "end": T(135),
        "headline": words(
            [("THE PHONE", 121.5), ("IS JUST", 122), ("A", 122.5), ("SENSOR", 123, True)]
        ),
        "headline_breaks_after": ["IS JUST"],
        "nodes": [
            {"id": "car", "label": "CARPLAY + iOS SHORTCUT", "t": T(124)},
            {"id": "api", "label": "FASTAPI ORCHESTRATOR", "t": T(124.5)},
            {"id": "voice", "label": "ELEVENLABS VOICE AGENT", "sub": "CLAUDE HAIKU", "t": T(125)},
            {"id": "neon", "label": "NEON JOB + DRIVE TABLES", "t": T(125.5)},
            {
                "id": "subs",
                "label": "SUBAGENTS",
                "sub": "CODER (CLAUDE CODE) · RESEARCH · EMAIL",
                "t": T(126),
            },
            {"id": "eta", "label": "ARRIVAL SCHEDULER", "sub": "GOOGLE ROUTES", "t": T(126.5)},
            {"id": "ntfy", "label": "NTFY", "t": T(127)},
        ],
        "packets": [
            ["car", "api", T(128)],
            ["api", "voice", T(129)],
            ["voice", "api", T(130)],
            ["api", "neon", T(130.5)],
            ["neon", "subs", T(131)],
            ["subs", "neon", T(132)],
            ["neon", "eta", T(132.5)],
            ["eta", "voice", T(133)],
            ["api", "ntfy", T(134)],
        ],
        "exit": T(134.5),
    },
    "K_safe": {
        "start": T(135),
        "end": T(146),
        "label": "SAFE BY DESIGN",
        "lines": [
            {"t": T(135.5), "text": "NOTHING IRREVERSIBLE WITHOUT A SPOKEN YES."},
            {"t": T(139), "text": "ONLY YOUR NUMBER. SHARED-SECRET TRIGGER."},
            {"t": T(142.5), "text": "IT ONLY RINGS WHEN IT'S WORTH IT."},
        ],
    },
    "L_stack": {
        "start": T(146),
        "end": T(148),
        "per_label_beats": 0.25,
        "labels": [
            "CARPLAY",
            "FASTAPI",
            "ELEVENLABS",
            "CLAUDE",
            "NEON",
            "GITHUB ACTIONS",
            "GOOGLE ROUTES",
            "NTFY",
        ],
    },
    "M_end": {
        "start": T(148),
        "end": 90.4,
        "mark_speed": 0.7,
        "wordmark": "SHOTGUN",
        "wordmark_t": T(149.5),
        "tagline": "an ai passenger for your car",
        "tagline_t": T(151),
        "small": "github.com/25danielc/shotgun · built at MHacks 2026",
        "small_t": T(152.5),
    },
}
Path(__file__).resolve().parents[1].joinpath("timeline.json").write_text(
    json.dumps(tl, indent=1, ensure_ascii=False) + "\n"
)
print("end global", 9.4 + 90.4)
