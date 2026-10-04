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
                    out[k] = self.g(v, p)
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
    e = R["E_ring"]
    for c in e["captions"]:
        caps.append({
            "start": e["start"] + c["start_src"] - e["src_in"] - 0.1,
            "end": e["start"] + c["end_src"] - e["src_in"],
            "text": c["text"], "speaker": c["speaker"]})  # fmt: skip
    for clip in R["F_talk"]["clips"]:
        caps += clip_captions(clip["t"], clip["in"], clip["out"], clip["speaker"], clip["captions"])
    a = R["H_arrival"]["audio"]
    caps += clip_captions(a["t"], a["in"], a["out"], "SHOTGUN", a["captions"])
    for c in caps:
        for line in c["text"].split("\n"):
            assert len(line) + (9 if c["speaker"] else 0) <= 52, line
        assert c["text"].count("\n") <= 1, c["text"]
    return sorted(caps, key=lambda c: c["start"])


def clip_captions(at, src_in, src_out, speaker, parts):
    out, start = [], at - 0.12  # lead the words slightly
    for i, part in enumerate(parts):
        until = part.get("until_src", src_out)
        end = at + until - src_in
        if i == len(parts) - 1:
            end += 0.35  # let the last line breathe
        out.append({"start": round(start, 3), "end": round(end, 3), "text": part["text"],
                    "speaker": None if speaker == "DANIEL" else speaker})  # fmt: skip
        start = end
    return out


def speech_clips(R: dict) -> list[dict]:
    """Placed speech for the mix and for the call-card waveform."""
    clips = [{"file": "work/audio/intro.wav", "at": 0.0, "in": 0.0, "out": R["intro"]["out"] -
              R["intro"]["in"], "kind": "intro"}]  # fmt: skip
    d, e = R["D_plugin"], R["E_ring"]
    car0 = 3.0  # work/audio/car.wav starts at 3.0 s of carplay_on.mov
    clips.append({"file": "work/audio/car.wav", "at": d["start"], "in": d["src_in"] - car0,
                  "out": d["src_in"] - car0 + d["end"] - d["start"], "kind": "car"})  # fmt: skip
    clips.append({"file": "work/audio/car.wav", "at": e["start"], "in": e["src_in"] - car0,
                  "out": e["src_in"] - car0 + e["end"] - e["start"], "kind": "ring"})  # fmt: skip
    for c in R["F_talk"]["clips"]:
        clips.append({"file": R["F_talk"]["audio_src"], "at": c["t"], "in": c["in"],
                      "out": c["out"], "kind": "call", "id": c["id"]})  # fmt: skip
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
    """Soft clicks for typed text, ticks for [DONE], whoosh and wipe for transitions."""
    ev = []

    def typed(t0, text, every=2):
        for i in range(0, len(text), every):
            ev.append({"sfx": f"key{1 + (i // every) % 3}", "t": round(t0 + i * TYPE_MS / 1000, 4)})

    ev.append({"sfx": "whoosh", "t": R["A_swipe"]["start"] + 0.19})
    ev.append({"sfx": "wipe", "t": R["C_problem"]["wipe"] + 0.15})
    lt = R["intro"]["lower_third"]
    typed(lt["t"], "┌─ " + lt["text"] + " ─┐")
    f = R["F_talk"]
    for line in f["panel_lines"]:
        if line.get("kind") in (None, "say"):
            typed(line["t"], line["text"])
        if line.get("done"):
            ev.append({"sfx": "tick", "t": line["t"] + len(line["text"]) * TYPE_MS / 1000 + 0.1})
        if line.get("kind") == "tag":
            ev.append({"sfx": "tick", "t": line["t"]})
    beat = 60 / 105
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
        (R["F_talk"]["start"], "Talk"),
        (R["G_subagents"]["start"], "Background work: subagents"),
        (R["H_arrival"]["start"], "Arrival call"),
        (R["J_how"]["start"], "How it works"),
        (R["K_safe"]["start"], "Safety"),
    ]
    # YouTube needs >= 3 chapters, the first at 0:00, each at least 10 s long.
    yt = [
        (0, "Intro"),
        (R["D_plugin"]["start"], "Plug in, it calls you"),
        (R["F_talk"]["start"], "Talk"),
        (R["G_subagents"]["start"], "Background work: subagents"),
        (R["H_arrival"]["start"], "Arrival call"),
        (R["J_how"]["start"], "How it works, safety"),
    ]
    lines = ["# Paste into the YouTube description (every chapter is at least 10 s, as YouTube "
             "requires):"]  # fmt: skip
    lines += [f"{mmss(t)} {name}" for t, name in yt]
    lines += ["", "# Full list as requested (Plug in and How it works are under 10 s, so YouTube "
              "won't show these as chapters):"]  # fmt: skip
    lines += [f"{mmss(t)} {name}" for t, name in full]
    return "\n".join(lines) + "\n"


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
    print(f"duration {R['duration']:.2f} s, {len(caps)} captions, {len(plan['sfx'])} sfx")
    for where, before, after in res.moves:
        print(f"  snap moved {where}: T {before:.3f} -> {after:.3f} ({after - before:+.3f} s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
