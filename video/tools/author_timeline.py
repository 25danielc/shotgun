"""Write video/timeline.json from the beat plan (beats -> T seconds), cut 2 (140 BPM).

Cut 2 puts the agent in the car: the plug-in selfie, the real dash recording of drive #16
(plug-in, ring, "Land City, about seven minutes"), and the driving footage under the drive #8
call and the dashboard, with windows running side by side. After running this,
timeline.json is the source of truth for fine re-times; re-run this only to re-plan.
"""

import json
from pathlib import Path

BPM = 140
B = 60 / BPM


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


# Driving footage (IMG_0353, drive #16): steady, well-exposed stretches (source seconds).
DRIVE = {"a": 0.2, "b": 13.0, "c": 22.0, "d": 42.5, "e": 46.5, "f": 49.0, "g": 33.0}

tl = {
    "_doc": (
        "All times are T seconds after the intro clip ends (global = intro length + T), "
        "except intro.* (seconds into the intro clip) and *_src / in / out (seconds into a "
        "source file). snap=true moves every T to the nearest half-beat of the music grid "
        "(bpm, downbeat_T) at build time; tools/build.py reports moves > 0.25 s."
    ),
    "fps": 30,
    "width": 1920,
    "height": 1080,
    "snap": True,
    "music": {
        "bpm": BPM,
        "downbeat_T": 0.4,
        "file": "work/audio/music_trap.wav",
        "duck_speech_db": -10,
        "duck_ring_db": -8,
        "fade_out_end_T": 90.4,
    },
    "end_T": 90.4,
    "grade": {
        "drive": "brightness(1.45) contrast(1.08) saturate(0.9)",
        "dash2": "brightness(1.25) contrast(1.05)",
    },
    "intro": {
        "src": "footage/self_start.mov",
        "in": 2.6,
        "out": 12.0,
        "lower_third": {"text": "DANIEL CHEN · BUILDER", "t": 1.0, "exit": 4.0},
        # the three agents he names, popping out on a line from his fingertips
        "logos": [
            {
                "id": "muse",
                "t": 2.62,
                "from": [995, 455],
                "to": [330, 250],
                "logo": "logos/meta_white.svg",
                "name": "MUSE",
                "by": "META",
            },
            {
                "id": "instinct",
                "t": 3.02,
                "from": [905, 470],
                "to": [1590, 250],
                "logo": "logos/instinct_white.png",
                "name": "INSTINCT",
                "by": "INSTINCT",
            },
            {
                "id": "dots",
                "t": 6.45,
                "from": [895, 480],
                "to": [1590, 560],
                "logo": "logos/openai_symbol_white.svg",
                "name": "DOTS",
                "by": "OPENAI",
            },
        ],
        "logos_dim": 7.7,
        "logos_exit": 8.9,
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
    "A_swipe": {"start": 0.02, "end": T(0), "nosnap": True},
    "B_thesis": {
        "start": T(0),
        "end": T(10),
        "mark_size": 240,
        "mark_slide": T(2.5),
        "mark_small": 96,
        "headline": words(
            [("YOUR", 3), ("CAR", 3.5), ("HAS A", 4), ("PASSENGER", 5, True), ("NOW", 6)]
        ),
        "headline_breaks_after": ["CAR"],
        "exit": T(9.5),
    },
    "C_problem": {
        "start": T(10),
        "end": T(18),
        "headline1": words([("YOU DRIVE", 10.5), ("HOURS", 11.5, True), ("A WEEK", 12)]),
        "headline1_breaks_after": ["YOU DRIVE"],
        "callout1": {"text": "time you can't spend on a screen", "t": T(12.5)},
        "wipe": T(14),
        "headline2": words([("YOUR AI", 14.5), ("CAN'T", 15), ("COME", 15.5, True)]),
        "headline2_breaks_after": ["YOUR AI"],
        "exit": T(17.5),
    },
    # D: split screen. The selfie (IMG_0354, drive #15) runs in a window while the dash
    # recording (drive #16) shows the phone going in, then CarPlay coming up.
    "D_plugin": {
        "start": T(18),
        "end": T(32),
        "dash2": [{"t": T(18), "src": 0.8}, {"t": T(28), "src": 8.4}],
        "dash2_cam": {"origin": [700, 400], "scale": 1.55, "push": 0.06},
        "selfie": {
            "t": T(18.5),
            "in": 0.0,
            "out": 4.1,
            "rect": [96, 236, 400, 711],
            "title": "┌─ YOU · IN THE CAR ─┐",
            "exit": T(28),
        },
        "headline": words([("PLUG", 19), ("IN.", 19.5, True)]),
        "callout": {
            "text": "CARPLAY_CONNECTED → /EVENTS",
            "t": T(29.5),
            "target": [705, 365],
            "label_off": [-120, 330],
        },
        "captions": [
            {
                "start_src": 0.0,
                "end_src": 4.1,
                "speaker": None,
                "text": "Alright, we just got in my car,\nso I'm going to connect via CarPlay.",
            }
        ],
    },
    # E: the ring, on the sharp close-up take (carplay_on.mov, drive #12).
    "E_ring": {
        "start": T(32),
        "end": T(44),
        "src": "footage/carplay_on.mov",
        "src_in": 10.371,
        "headline": words([("IT", 33.5), ("CALLS", 34, True), ("YOU", 34.5)]),
        "headline_breaks_after": ["CALLS"],
        "exit": T(43.5),
        "callouts": [
            {
                "text": "ELEVENLABS AGENT · TWILIO OUTBOUND",
                "t": T(36),
                "target": [940, 455],
                "label_off": [140, -330],
                "exit": T(43.5),
            },
            {
                "text": "CARPLAY ON → RING 4.8 S",
                "t": T(38.5),
                "target": [1195, 605],
                "label_off": [80, 230],
                "exit": T(43.5),
            },
        ],
        "captions": [],
    },
    # E2: the real drive #16 departure call over the dash recording (on-call screen).
    "E2_where": {
        "start": T(44),
        "end": T(63),
        "audio_src": "work/audio/d16_departure.wav",
        "dash2_call_start": 21.5,
        "dash2_cam": {"origin": [705, 365], "scale": 1.9, "push": 0.05},
        "headline": words([("SAY", 45), ("WHERE.", 45.5, True)]),
        "exit": T(62.5),
        "clips": [
            {
                "id": "g1",
                "t": T(44.5),
                "in": 0.30,
                "out": 2.40,
                "speaker": "SHOTGUN",
                "captions": [{"text": "Shotgun here. Where are we headed?"}],
            },
            {
                "id": "g2",
                "t": T(50),
                "in": 6.00,
                "out": 6.90,
                "speaker": "DANIEL",
                "captions": [{"text": "Land City."}],
            },
            {
                "id": "g3",
                "t": T(52.5),
                "in": 8.35,
                "out": 9.55,
                "speaker": "SHOTGUN",
                "captions": [{"text": "Let me look that up real quick."}],
            },
            {
                "id": "g4",
                "t": T(56),
                "in": 14.55,
                "out": 17.45,
                "speaker": "SHOTGUN",
                "captions": [{"text": "Got it. Land City, about seven minutes."}],
            },
        ],
        "panel": {"rect": [1180, 300, 644, 330], "title": "┌─ CALL · DRIVE #16 ─┐"},
        "panel_lines": [
            {"t": T(50), "who": "you", "text": "land city"},
            {"t": T(53), "kind": "chip", "text": "set_destination · 5.4 s", "done": True},
            {"t": T(60), "kind": "tag", "text": "eta 7 min · arrival call at eta − 3"},
        ],
    },
    # F: the drive #8 call over the driving footage, with the call card and panel in windows.
    "F_talk": {
        "start": T(63),
        "end": T(110),
        "audio_src": "work/audio/d8_departure.wav",
        "call_started_src": 0.0,
        "broll": [{"t": T(63 + 8 * i), "src": DRIVE[k]} for i, k in enumerate("abcdef")],
        "headline": words([("TALK.", 63.5), ("IT", 64, True), ("HANDLES", 64.5), ("IT", 65)]),
        "headline_breaks_after": ["IT"],
        "exit": T(109.5),
        "card": {"rect": [96, 560, 760, 330], "title": "┌─ REAL CALL · DRIVE #8 ─┐"},
        "panel": {"rect": [1120, 300, 704, 560], "title": "┌─ CALL ─┐"},
        "clips": [
            {
                "id": "c1",
                "t": T(63.5),
                "in": 17.33,
                "out": 19.62,
                "speaker": "DANIEL",
                "captions": [{"text": "Can you find me some ramen places nearby?"}],
            },
            {
                "id": "c2",
                "t": T(69.5),
                "in": 25.05,
                "out": 27.80,
                "speaker": "SHOTGUN",
                "captions": [{"text": "You've got Tomukun Noodle Bar\nand Slurping Turtle."}],
            },
            {
                "id": "c3",
                "t": T(76.5),
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
                "t": T(95.5),
                "in": 90.36,
                "out": 92.30,
                "speaker": "SHOTGUN",
                "captions": [{"text": "Want me to merge it if the tests pass?"}],
            },
            {
                "id": "c5",
                "t": T(100.5),
                "in": 93.10,
                "out": 94.25,
                "speaker": "DANIEL",
                "captions": [{"text": "Uh, yeah, that'd be great."}],
            },
        ],
        "panel_lines": [
            {"t": T(63.5), "who": "you", "text": "find me some ramen places nearby"},
            {"t": T(69), "kind": "chip", "text": "search_web · 3073 ms", "done": True},
            {"t": T(89), "who": "you", "text": "fix the email capitalization bug"},
            {"t": T(95.5), "who": "shotgun", "text": "merge it if the tests pass?"},
            {"t": T(100.5), "who": "you", "text": "yeah, that'd be great"},
            {"t": T(103.5), "kind": "tag", "text": "preapproval: require_tests_pass"},
            {
                "t": T(104.5),
                "kind": "chip",
                "text": "dispatch_task → coder subagent · 165 ms",
                "done": True,
            },
        ],
    },
    "G_subagents": {
        "start": T(110),
        "end": T(142),
        "headline": words(
            [
                ("SUBAGENTS", 110.5, True),
                ("WORK", 111),
                ("WHILE", 111.5),
                ("YOU", 112),
                ("DRIVE", 112.5),
            ]
        ),
        "headline_breaks_after": ["WORK"],
        "exit": T(115.5),
        "broll_full": {"t": T(110), "src": DRIVE["g"]},
        "pip": {"rect": [1344, 730, 480, 270], "title": "┌─ YOU · DRIVING ─┐", "src": DRIVE["c"]},
        "windows": [
            {
                "id": "jobs",
                "t": T(116),
                "rect": [505, 12, 777, 300],
                "at": [820, 430],
                "room": [1200, 520],
                "replay": [
                    [0, "22:26:44"],
                    [0.75, "22:26:47"],
                    [2.25, "22:27:52"],
                    [3.75, "22:28:06"],
                    [5.25, "22:28:10"],
                    [6, "22:28:11"],
                ],
                "done_ticks_beats": [0.75, 2.25, 2.25, 3.75, 3.75, 5.25],
                "callouts": [
                    {
                        "text": "STEP-LEVEL STATE · NEON POSTGRES",
                        "t_beat": 1.0,
                        "target": [538, 94],
                        "label_off": [40, -150],
                    }
                ],
            },
            {
                "id": "agents",
                "t": T(122),
                "rect": [6, 832, 493, 212],
                "at": [820, 470],
                "room": [1200, 560],
                "replay": [[0, "22:26:40"], [1.5, "22:26:46"], [6, "22:26:52"]],
                "callouts": [
                    {
                        "text": "1 VOICE AGENT → 3 SUBAGENTS",
                        "t_beat": 1.5,
                        "target": [38, 859],
                        "label_off": [80, -170],
                    },
                    {
                        "text": "CLAUDE CODE VIA GITHUB ACTIONS",
                        "t_beat": 3.0,
                        "target": [113, 900],
                        "label_off": [420, 300],
                    },
                ],
            },
            {
                "id": "tools",
                "t": T(128),
                "rect": [505, 832, 777, 212],
                "at": [820, 470],
                "room": [1200, 560],
                "replay": [[0, "22:25:14"], [5, "22:27:16"]],
                "callouts": [
                    {
                        "text": "INLINE TOOLS < 8 S · BACKGROUND < 500 MS",
                        "t_beat": 1.0,
                        "target": [760, 860],
                        "label_off": [40, -250],
                    }
                ],
            },
            {
                "id": "log",
                "t": T(133),
                "rect": [1288, 12, 626, 520],
                "at": [820, 470],
                "room": [1200, 560],
                "replay": [[0, "22:26:40"], [4, "22:28:12"]],
                "push_in": True,
            },
            {
                "id": "services",
                "t": T(137),
                "rect": [1288, 832, 626, 212],
                "at": [820, 470],
                "room": [1200, 560],
                "replay": [[0, "22:28:15"], [5, "22:28:17"]],
                "callouts": [
                    {
                        "text": "HEALTH-CHECKED EVERY 30 S",
                        "t_beat": 1.0,
                        "target": [1437, 860],
                        "label_off": [80, -200],
                    }
                ],
            },
        ],
    },
    "H_arrival": {
        "start": T(142),
        "end": T(162),
        "countdown": {
            "t": T(142),
            "until": T(148),
            "rect": [6, 12, 493, 210],
            "at": [820, 500],
            "room": [1200, 560],
            "replay": [[0, "22:32:03"], [5.0, "22:32:08"], [6, "22:32:09"]],
            "callout": {
                "text": "ETA − 3 MIN · GOOGLE ROUTES",
                "t_beat": 0.5,
                "target": [110, 142],
                "label_off": [300, 240],
            },
        },
        "pip": {"rect": [1344, 730, 480, 270], "title": "┌─ YOU · DRIVING ─┐", "src": DRIVE["d"]},
        "broll_full": {"src": DRIVE["f"]},
        "card": {"rect": [560, 330, 800, 380], "title": "┌─ REAL CALL · ARRIVAL · DRIVE #8 ─┐"},
        "headline1": words([("ONE CALL", 142.5), ("TO HAND", 143), ("IT OFF", 143.5)]),
        "headline2": words(
            [("ONE CALL", 148.5), ("BEFORE", 149), ("YOU", 149.5), ("PARK", 150, True)]
        ),
        "exit1": T(147.5),
        "exit2": T(161.5),
        "audio": {
            "t": T(148),
            "src": "work/audio/d8_arrival.wav",
            "in": 0.35,
            "out": 6.30,
            "captions": [
                {"text": "Shotgun here.\nYou're about three minutes out.", "until_src": 2.85},
                {"text": "Got the email capitalization fix merged\nand tests came through."},
            ],
        },
        "overlay": {"text": "PR #11 MERGED · TESTS PASSED", "t": T(159)},
    },
    "I_parked": {
        "start": T(162),
        "end": T(171),
        "bg_dash2_src": 45.5,
        "recap_title": "Shotgun: drive recap",
        "recap_lines": [
            "Done: I merged the fix for the email capitalization bug, and the tests passed.",
            "Done: I sent your email to Daniel.",
        ],
        "headline": words([("PARK.", 162.5), ("READ", 163, True), ("LATER.", 163.5)]),
        "callout": {"text": "UNPLUG → NTFY RECAP. NO CALL.", "t": T(165)},
    },
    "J_how": {
        "start": T(171),
        "end": T(185),
        "headline": words(
            [("THE PHONE", 171.5), ("IS JUST", 172), ("A", 172.5), ("SENSOR", 173, True)]
        ),
        "headline_breaks_after": ["IS JUST"],
        "nodes": [
            {"id": "car", "label": "CARPLAY + iOS SHORTCUT", "t": T(174)},
            {"id": "api", "label": "FASTAPI ORCHESTRATOR", "t": T(174.5)},
            {"id": "voice", "label": "ELEVENLABS VOICE AGENT", "sub": "CLAUDE HAIKU", "t": T(175)},
            {"id": "neon", "label": "NEON JOB + DRIVE TABLES", "t": T(175.5)},
            {
                "id": "subs",
                "label": "SUBAGENTS",
                "sub": "CODER (CLAUDE CODE) · RESEARCH · EMAIL",
                "t": T(176),
            },
            {"id": "eta", "label": "ARRIVAL SCHEDULER", "sub": "GOOGLE ROUTES", "t": T(176.5)},
            {"id": "ntfy", "label": "NTFY", "t": T(177)},
        ],
        "packets": [
            ["car", "api", T(178)],
            ["api", "voice", T(179)],
            ["voice", "api", T(180)],
            ["api", "neon", T(180.5)],
            ["neon", "subs", T(181)],
            ["subs", "neon", T(182)],
            ["neon", "eta", T(182.5)],
            ["eta", "voice", T(183)],
            ["api", "ntfy", T(184)],
        ],
        "exit": T(184.5),
    },
    "K_safe": {
        "start": T(185),
        "end": T(197),
        "label": "SAFE BY DESIGN",
        "lines": [
            {"t": T(185.5), "text": "NOTHING IRREVERSIBLE WITHOUT A SPOKEN YES."},
            {"t": T(189), "text": "ONLY YOUR NUMBER. SHARED-SECRET TRIGGER."},
            {"t": T(192.5), "text": "IT ONLY RINGS WHEN IT'S WORTH IT."},
        ],
    },
    "L_stack": {
        "start": T(197),
        "end": T(199),
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
        "start": T(199),
        "end": 90.4,
        "mark_speed": 0.7,
        "wordmark": "SHOTGUN",
        "wordmark_t": T(200.5),
        "tagline": "an ai passenger for your car",
        "tagline_t": T(202),
        "small": "github.com/25danielc/shotgun · built at MHacks 2026",
        "small_t": T(203.5),
    },
}
Path(__file__).resolve().parents[1].joinpath("timeline.json").write_text(
    json.dumps(tl, indent=1, ensure_ascii=False) + "\n"
)
print("end global", round(9.4 + tl["end_T"], 2), "· M starts T", T(199))
