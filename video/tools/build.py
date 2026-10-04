"""Resolve video/timeline.json into what the renderer and the mixer read.

    uv run --no-project --with numpy python video/tools/build.py

timeline.json keeps every time as T, in seconds after the intro clip. This script:
- moves each T to the nearest half-beat of the music grid when snap is true, and prints every
  move larger than 0.25 s;
- converts T to global seconds (global = intro length + T);
- writes comp/timeline.js (window.TL, window.ENV, window.CAPS) for the page;
- writes work/mix_plan.json (speech clips, SFX events, duck regions) for tools/mix.py;
- writes out/shotgun_demo.srt and out/chapters.txt.

Fields holding T: t, start, end, exit, exit1, exit2, mark_slide, wipe, until, wordmark_t,
tagline_t, small_t, end_T, fade_out_end_T, and the third item of each J packet.
Source-file seconds (in, out, src_in, *_src) and beat offsets (t_beat, replay keys) are left
alone. intro.* times are seconds into the intro clip, which are also global seconds.
"""

from __future__ import annotations

import json
import wave
from pathlib import Path

import numpy as np

VIDEO = Path(__file__).resolve().parents[1]
T_KEYS = {"t", "start", "end", "exit", "exit1", "exit2", "mark_slide", "wipe", "until",
          "wordmark_t", "tagline_t", "small_t", "end_T", "fade_out_end_T"}  # fmt: skip
TYPE_MS = 30  # type_line: ms per character
CALLOUT_MS = 22


def load_wav(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path)) as w:
        sr = w.getframerate()
        x = np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32) / 32768
        if w.getnchannels() > 1:
            x = x.reshape(-1, w.getnchannels()).mean(1)
    return x, sr


class Resolver:
    def __init__(self, tl: dict):
        self.tl = tl
        intro = tl["intro"]
        self.offset = intro["out"] - intro["in"]
        m = tl["music"]
        self.beat = 60 / m["bpm"]
        self.downbeat = m["downbeat_T"]
        self.moves: list[tuple[str, float, float]] = []

    def snap(self, T: float, where: str) -> float:
        if not self.tl.get("snap"):
            return T
        half = self.beat / 2
        k = round((T - self.downbeat) / half)
        snapped = self.downbeat + k * half
        if abs(snapped - T) > 0.25:
            self.moves.append((where, T, snapped))
        return snapped

    def g(self, T: float, where: str = "") -> float:
        return round(self.offset + self.snap(T, where), 4)

    def walk(self, node, path="", in_intro=False):
        if isinstance(node, dict):
            out = {}
            for k, v in node.items():
                p = f"{path}.{k}" if path else k
                intro = in_intro or k == "intro"
                if k in T_KEYS and isinstance(v, (int, float)) and not intro:
                    # "nosnap": times tied to a cut, not to the beat (the swipe off the intro)
                    out[k] = round(self.offset + v, 4) if node.get("nosnap") else self.g(v, p)
                elif k == "packets":
                    out[k] = [[a, b, self.g(t, p)] for a, b, t in v]
                else:
                    out[k] = self.walk(v, p, intro)
            return out
        if isinstance(node, list):
            return [self.walk(v, f"{path}[{i}]", in_intro) for i, v in enumerate(node)]
        return node


def captions(R: dict) -> list[dict]:
    """Every spoken line, in global seconds, max 2 lines of 42 characters."""
    caps = []
    for c in R["intro"]["captions"]:
        caps.append({"start": c["start"], "end": c["end"], "text": c["text"], "speaker": None})
    D = R["D_plugin"]
    sw = D["selfie"]
    for c in D["captions"]:
        caps.append({
            "start": max(D["start"], sw["t"] + c["start_src"] - sw["in"] - 0.1),
            "end": min(sw["exit"], sw["t"] + c["end_src"] - sw["in"] + 0.3),
            "text": c["text"], "speaker": c["speaker"]})  # fmt: skip
    for key in ("E2_where", "F_talk"):
        S = R[key]
        for clip in S["clips"]:
            caps += clip_captions(
                clip["t"], clip["in"], clip["out"], clip["speaker"], clip["captions"],
                window=(S["start"], S["end"]),
            )  # fmt: skip
    H = R["H_arrival"]
    a = H["audio"]
    caps += clip_captions(
        a["t"], a["in"], a["out"], "SHOTGUN", a["captions"], window=(a["t"], H["end"])
    )
    caps = sorted(caps, key=lambda c: c["start"])
    for prev, nxt in zip(caps, caps[1:], strict=False):  # never two captions at once
        prev["end"] = min(prev["end"], nxt["start"])
    for c in caps:
        for line in c["text"].split("\n"):
            assert len(line) + (9 if c["speaker"] else 0) <= 52, line
        assert c["text"].count("\n") <= 1, c["text"]
    return caps


def clip_captions(at, src_in, src_out, speaker, parts, window=(0.0, 1e9)):
    """Captions for one placed clip; they never cross the cuts of the shot they belong to."""
    out, start = [], max(window[0], at - 0.12)  # lead the words slightly
    for i, part in enumerate(parts):
        until = part.get("until_src", src_out)
        end = at + until - src_in
        if i == len(parts) - 1:
            end += 0.35  # let the last line breathe
        end = min(end, window[1])
        out.append({"start": round(start, 3), "end": round(end, 3), "text": part["text"],
                    "speaker": None if speaker == "DANIEL" else speaker})  # fmt: skip
        start = end
    return out


def speech_clips(R: dict) -> list[dict]:
    """Placed audio for the mix (and, for kind "call", the call-card waveform)."""
    intro_len = R["intro"]["out"] - R["intro"]["in"]
    clips = [
        {"file": "work/audio/intro.wav", "at": 0.0, "in": 0.0, "out": intro_len, "kind": "intro"}
    ]
    D = R["D_plugin"]
    cuts = D["dash2"] + [{"t": D["end"], "src": None}]
    for c, nxt in zip(cuts, cuts[1:], strict=False):  # the dash recording's own sound, quietly
        clips.append({"file": "work/audio/dash2.wav", "at": c["t"], "in": c["src"],
                      "out": c["src"] + nxt["t"] - c["t"], "kind": "car"})  # fmt: skip
    sw = D["selfie"]
    clips.append({"file": "work/audio/selfie.wav", "at": sw["t"], "in": sw["in"], "out": sw["out"],
                  "kind": "intro"})  # fmt: skip
    e = R["E_ring"]
    car0 = 3.0  # work/audio/car.wav starts at 3.0 s of carplay_on.mov
    clips.append({"file": "work/audio/car.wav", "at": e["start"], "in": e["src_in"] - car0,
                  "out": e["src_in"] - car0 + e["end"] - e["start"], "kind": "ring"})  # fmt: skip
    for key in ("E2_where", "F_talk"):
        for c in R[key]["clips"]:
            clips.append({"file": R[key]["audio_src"], "at": c["t"], "in": c["in"], "out": c["out"],
                          "kind": "call", "id": c["id"]})  # fmt: skip
    a = R["H_arrival"]["audio"]
    clips.append({"file": a["src"], "at": a["t"], "in": a["in"], "out": a["out"], "kind": "call",
                  "id": "a1"})  # fmt: skip
    return clips


def envelope(clips: list[dict], total: float, fps: int) -> list[float]:
    """Per-frame RMS (0..1) of the call audio only, for the call card's waveform."""
    env = np.zeros(int(total * fps) + 2)
    cache: dict[str, tuple[np.ndarray, int]] = {}
    for c in clips:
        if c["kind"] != "call":
            continue
        x, sr = cache.setdefault(c["file"], load_wav(VIDEO / c["file"]))
        seg = x[int(c["in"] * sr) : int(c["out"] * sr)]
        hop = sr // fps
        for i in range(len(seg) // hop):
            f = int(round(c["at"] * fps)) + i
            rms = float(np.sqrt(np.mean(seg[i * hop : (i + 1) * hop] ** 2)))
            env[f] = max(env[f], rms)
    peak = np.percentile(env[env > 0], 98) if (env > 0).any() else 1
    return [round(float(v), 3) for v in np.clip(env / peak, 0, 1)]


def sfx_events(R: dict) -> list[dict]:
    """Soft clicks for typed text, ticks for [DONE] and pops, whoosh and wipe for transitions."""
    ev = []

    def typed(t0, text, every=2):
        for i in range(0, len(text), every):
            ev.append({"sfx": f"key{1 + (i // every) % 3}", "t": round(t0 + i * TYPE_MS / 1000, 4)})

    ev.append({"sfx": "whoosh", "t": R["A_swipe"]["start"] + 0.19})
    ev.append({"sfx": "wipe", "t": R["C_problem"]["wipe"] + 0.15})
    lt = R["intro"]["lower_third"]
    typed(lt["t"], "┌─ " + lt["text"] + " ─┐")
    for logo in R["intro"]["logos"]:
        ev.append({"sfx": "tick", "t": logo["t"] + 0.2})
    for key in ("E2_where", "F_talk"):
        for line in R[key]["panel_lines"]:
            if line.get("kind") in (None, "chip"):
                typed(line["t"], line["text"])
            if line.get("done"):
                ev.append(
                    {"sfx": "tick", "t": line["t"] + len(line["text"]) * TYPE_MS / 1000 + 0.1}
                )
            if line.get("kind") == "tag":
                ev.append({"sfx": "tick", "t": line["t"]})
    beat = 60 / R["music"]["bpm"]
    jobs = R["G_subagents"]["windows"][0]
    for b in sorted(set(jobs.get("done_ticks_beats", []))):
        ev.append({"sfx": "tick", "t": jobs["t"] + b * beat})
    ev.append({"sfx": "tick", "t": R["H_arrival"]["overlay"]["t"]})
    for line in R["K_safe"]["lines"]:
        typed(line["t"], "> " + line["text"])
        ev.append({"sfx": "tick", "t": line["t"] + (len(line["text"]) + 2) * TYPE_MS / 1000 + 0.15})
    m = R["M_end"]
    typed(m["tagline_t"], m["tagline"])
    return sorted(ev, key=lambda e: e["t"])


def srt(caps: list[dict]) -> str:
    def ts(s):
        ms = int(round(s * 1000))
        return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"

    out = []
    for i, c in enumerate(caps, 1):
        text = (f"{c['speaker']}: " if c["speaker"] else "") + c["text"]
        out.append(f"{i}\n{ts(c['start'])} --> {ts(c['end'])}\n{text}\n")
    return "\n".join(out)


def chapters(R: dict) -> str:
    def mmss(s):
        return f"{int(s) // 60}:{int(s) % 60:02d}"

    full = [
        (0, "Intro"),
        (R["D_plugin"]["start"], "Plug in"),
        (R["E_ring"]["start"], "It calls you"),
        (R["E2_where"]["start"], "Talk"),
        (R["G_subagents"]["start"], "Background work: subagents"),
        (R["H_arrival"]["start"], "Arrival call"),
        (R["J_how"]["start"], "How it works"),
        (R["K_safe"]["start"], "Safety"),
    ]
    # YouTube needs >= 3 chapters, the first at 0:00, each at least 10 s long.
    yt = [
        (0, "Intro"),
        (R["D_plugin"]["start"], "Plug in, it calls you"),
        (R["E2_where"]["start"], "Talk"),
        (R["G_subagents"]["start"], "Background work: subagents"),
        (R["H_arrival"]["start"], "Arrival call"),
        (R["J_how"]["start"], "How it works, safety"),
    ]
    lines = ["# Paste into the YouTube description (every chapter is at least 10 s, as YouTube "
             "requires):"]  # fmt: skip
    lines += [f"{mmss(t)} {name}" for t, name in yt]
    lines += ["", "# Full list as requested (some are under 10 s, so YouTube won't show them as "
              "chapters):"]  # fmt: skip
    lines += [f"{mmss(t)} {name}" for t, name in full]
    return "\n".join(lines) + "\n"


SHOTS = [  # key, name, presets, source
    ("A_swipe", "A swipe", "swipe_down + green seam, whoosh", "intro last frame → black"),
    ("B_thesis", "B thesis", "mark_build, IO slide, word_in/out", "graphic"),
    ("C_problem", "C problem", "word_in, scan_wipe, word_out", "dot grid"),
    (
        "D_plugin",
        "D plug in",
        "split: dash push_in + selfie window, callout",
        "new_screen.mov + IMG_0354",
    ),
    ("E_ring", "E it calls you", "push_in 1.00→1.08, word_in, callouts", "carplay_on.mov"),
    (
        "E2_where",
        "E2 say where",
        "dash push_in, call panel window, captions",
        "new_screen.mov + drive #16 call",
    ),
    (
        "F_talk",
        "F talk",
        "b-roll cuts on the beat, call card + panel windows",
        "IMG_0353 + drive #8 call",
    ),
    (
        "G_subagents",
        "G subagents",
        "punch (420 ms IO) + push_in, live driving window",
        "dashboard replay + IMG_0353",
    ),
    (
        "H_arrival",
        "H arrival",
        "countdown punch + driving window, call card over b-roll",
        "replay + IMG_0353 + drive #8 call",
    ),
    (
        "I_parked",
        "I parked",
        "scale 0.96→1.0 (OUT 400 ms), callout",
        "recap_text(drive #8) over new_screen.mov",
    ),
    ("J_how", "J how it works", "nodes draw (leader preset), packets LIN 600 ms", "graphic"),
    ("K_safe", "K safe", "type_line + tag_done", "graphic"),
    ("L_stack", "L stack", "one label per 1/4 beat", "graphic"),
    ("M_end", "M end card", "mark_build ×0.7, letter word_in 40 ms, type_line", "graphic"),
]


def keyframes(R: dict) -> str:
    """out/keyframes.md: the timeline as built, in global seconds (and T after the intro)."""
    off = R["intro_len"]
    beat = 60 / R["music"]["bpm"]

    def events(S):
        found = []

        def walk(n, path=""):
            if isinstance(n, dict):
                label = n.get("text") or n.get("id") or n.get("name")
                if label and isinstance(n.get("t"), (int, float)):
                    found.append(f"{label} @{n['t']:.2f}")
                for k, v in n.items():
                    walk(v, k)
            elif isinstance(n, list):
                for v in n:
                    walk(v, path)

        walk(S)
        return "; ".join(found[:14]) + (" …" if len(found) > 14 else "")

    rows = [
        f"| INTRO | {0:6.2f} | {R['A_swipe']['start']:6.2f} | {-off:6.2f} | — | "
        "logos on finger lines, "
        f"lower third, captions | self_start.mov {R['intro']['in']:.2f}–{R['intro']['out']:.2f} | "
        + "; ".join(f"{x['name']} @{x['t']:.2f}" for x in R["intro"]["logos"])
        + " |"
    ]
    for key, name, presets, source in SHOTS:
        S = R[key]
        end = R["duration"] if key == "M_end" else S["end"]
        b = (S["start"] - off - R["music"]["downbeat_T"]) / beat
        rows.append(f"| {name} | {S['start']:6.2f} | {end:6.2f} | {S['start'] - off:6.2f} "
                    f"| {b:6.1f} "
                    f"| {presets} | {source} | {events(S)} |")  # fmt: skip
    head = (
        "# Keyframes (as built)\n\n"
        "Generated by tools/build.py from timeline.json. Times are global seconds; "
        f"T is seconds after the intro ({off:.2f} s); beat counts from the first downbeat "
        f"(T {R['music']['downbeat_T']}, {R['music']['bpm']} BPM). "
        "To re-time: edit timeline.json, then `build.py`, `mix.py`, `render.py`.\n\n"
        "| Shot | Start | End | T | Beat | Presets | Source | Events (global s) |\n"
        "|---|---|---|---|---|---|---|---|\n"
    )
    return head + "\n".join(rows) + "\n"


def main() -> int:
    tl = json.loads((VIDEO / "timeline.json").read_text())
    res = Resolver(tl)
    R = res.walk(tl)
    R["intro_len"] = res.offset
    R["duration"] = R["end_T"]
    fps = tl["fps"]
    clips = speech_clips(R)
    caps = captions(R)
    env = envelope(clips, R["duration"], fps)
    comp = VIDEO / "comp"
    comp.mkdir(exist_ok=True)
    (comp / "timeline.js").write_text(
        "// Generated by tools/build.py from timeline.json. Do not edit.\n"
        f"window.TL = {json.dumps(R, ensure_ascii=False)};\n"
        f"window.CAPS = {json.dumps(caps, ensure_ascii=False)};\n"
        f"window.ENV = {json.dumps(env)};\n"
    )
    plan = {
        "duration": R["duration"],
        "speech": clips,
        "sfx": sfx_events(R),
        "music": {**R["music"], "ring": [R["E_ring"]["start"], R["E_ring"]["end"]]},
    }
    (VIDEO / "work" / "mix_plan.json").write_text(json.dumps(plan, indent=1))
    out = VIDEO / "out"
    out.mkdir(exist_ok=True)
    (out / "shotgun_demo.srt").write_text(srt(caps))
    (out / "chapters.txt").write_text(chapters(R))
    (out / "keyframes.md").write_text(keyframes(R))
    print(f"duration {R['duration']:.2f} s, {len(caps)} captions, {len(plan['sfx'])} sfx")
    for where, before, after in res.moves:
        print(f"  snap moved {where}: T {before:.3f} -> {after:.3f} ({after - before:+.3f} s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
