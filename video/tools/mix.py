"""Mix the soundtrack from work/mix_plan.json (tools/build.py) into work/audio/mix.wav.

    uv run --no-project --with numpy --with scipy python video/tools/mix.py

- Speech: the intro, the real CarPlay take (D ambience, E ring and greeting), and the drive #8
  call clips. Each clip gets 15 ms fades, and the call audio (8 kHz-ish phone band) a gentle
  presence lift.
- Music: work/audio/music.wav, ducked 10 dB under speech (15 ms attack, 350 ms release) and
  8 dB through the ring shot, with a 150 Hz low shelf down 3 dB under talk so it never masks
  words. It fades out by the end.
- SFX at about -24 dB, placed by their peak (work/audio/music_report.json offsets).
- Master: two-pass loudnorm to -14 LUFS integrated with a -2 dBTP ceiling, so the AAC encode
  (which overshoots ~0.5 dB) still lands at or under -1 dBTP; the result is measured and printed.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import numpy as np
from scipy.io import wavfile
from scipy.signal import butter, sosfilt

VIDEO = Path(__file__).resolve().parents[1]
SR = 48000
AUD = VIDEO / "work" / "audio"


def read(path: Path) -> np.ndarray:
    sr, x = wavfile.read(path)
    x = x.astype(np.float32) / 32768.0 if x.dtype == np.int16 else x.astype(np.float32)
    if x.ndim == 1:
        x = np.stack([x, x], 1)
    if sr != SR:
        raise SystemExit(f"{path} is {sr} Hz, expected {SR}")
    return x


SPEECH_RMS_DB = -20.0  # every spoken clip, before the master loudnorm
MUSIC_BED_DB = -4.0  # the bed under everything; ducking comes on top


def soft_limit(x: np.ndarray, knee: float = 0.7) -> np.ndarray:
    """Leave everything under the knee alone; round off peaks above it (never past 1.0)."""
    over = np.abs(x) > knee
    y = x.copy()
    y[over] = np.sign(x[over]) * (
        knee + (1 - knee) * np.tanh((np.abs(x[over]) - knee) / (1 - knee))
    )
    return y


def fade(x: np.ndarray, ms: float = 15) -> np.ndarray:
    n = min(len(x) // 2, int(SR * ms / 1000))
    if n:
        ramp = np.linspace(0, 1, n, dtype=np.float32)[:, None]
        x[:n] *= ramp
        x[-n:] *= ramp[::-1]
    return x


def db(v: float) -> float:
    return 10 ** (v / 20)


def follower(env: np.ndarray, attack_ms=15, release_ms=350) -> np.ndarray:
    """Attack/release smoothing, run at a 1 kHz control rate and held back up to 48 kHz."""
    step = SR // 1000
    ctl = env[: len(env) // step * step].reshape(-1, step).max(1)
    a, r = np.exp(-1 / attack_ms), np.exp(-1 / release_ms)
    out = np.empty_like(ctl)
    y = 0.0
    for i, v in enumerate(ctl):
        c = a if v > y else r
        y = c * y + (1 - c) * v
        out[i] = y
    full = np.repeat(out, step)
    return np.pad(full, (0, len(env) - len(full)), mode="edge").astype(np.float32)


def main() -> int:
    plan = json.loads((VIDEO / "work" / "mix_plan.json").read_text())
    report = json.loads((AUD / "music_report.json").read_text())
    total = int(plan["duration"] * SR)
    speech = np.zeros((total, 2), np.float32)
    talk_mask = np.zeros(total, np.float32)
    cache: dict[str, np.ndarray] = {}
    presence = butter(2, [1500, 4000], btype="bandpass", fs=SR, output="sos")
    for c in plan["speech"]:
        src = cache.setdefault(c["file"], read(VIDEO / c["file"]))
        seg = src[int(c["in"] * SR) : int(c["out"] * SR)].copy()
        gain = {"intro": 1.0, "car": 0.55, "ring": 0.9, "call": 1.0}[c["kind"]]
        if c["kind"] == "call":
            seg = seg + 0.35 * sosfilt(presence, seg, axis=0).astype(np.float32)
        if c["kind"] in ("intro", "call"):
            # level-match every spoken clip (the two sides of a phone call differ by ~8 dB)
            rms = float(np.sqrt(np.mean(seg**2))) + 1e-9
            gain = db(SPEECH_RMS_DB) / rms
        seg = soft_limit(fade(seg * gain))
        a = int(c["at"] * SR)
        b = min(total, a + len(seg))
        speech[a:b] += seg[: b - a]
        if c["kind"] in ("intro", "call"):
            talk_mask[a:b] = 1.0
    music = read(Path(VIDEO / plan["music"]["file"]))[:total] * db(MUSIC_BED_DB)
    if len(music) < total:
        music = np.pad(music, ((0, total - len(music)), (0, 0)))

    # ducking: speech envelope (talk only) + the ring shot, smoothed
    env = follower(talk_mask)
    duck = 1 - env * (1 - db(plan["music"]["duck_speech_db"]))
    r0, r1 = (int(x * SR) for x in plan["music"]["ring"])
    ring = np.zeros(total, np.float32)
    ring[r0:r1] = 1
    ring = follower(ring, 80, 400)
    duck = np.minimum(duck, 1 - ring * (1 - db(plan["music"]["duck_ring_db"])))
    shelf = butter(2, 150, btype="lowpass", fs=SR, output="sos")
    low = sosfilt(shelf, music, axis=0).astype(np.float32)
    music = music - (env[:, None] * (1 - db(-3))) * low
    music *= duck[:, None]
    end = int(plan["music"]["fade_out_end_T"] * SR) if "fade_out_end_T" in plan["music"] else total
    fo = int(1.2 * SR)
    music[max(0, end - fo) : end] *= np.linspace(1, 0, min(fo, end), dtype=np.float32)[:, None]
    music[end:] = 0

    sfx = np.zeros((total, 2), np.float32)
    peaks = {k: v["peak_offset_s"] for k, v in report["sfx"].items()}
    for e in plan["sfx"]:
        name = e["sfx"]
        x = cache.setdefault("sfx:" + name, read(AUD / f"sfx_{name}.wav"))
        off = peaks[f"sfx_{name}"]
        a = int((e["t"] - off) * SR)
        if a < 0 or a >= total:
            continue
        b = min(total, a + len(x))
        level = {
            "key1": -26,
            "key2": -26,
            "key3": -26,
            "tick": -22,
            "whoosh": -20,
            "wipe": -24,
        }.get(name, -24)
        sfx[a:b] += x[: b - a] * db(level + 6)  # sfx files peak at -6 dBFS
    mix = speech + music + sfx
    # masking check: speech vs (ducked) music over every placed speech clip
    for c in plan["speech"]:
        a, b = int(c["at"] * SR), int((c["at"] + c["out"] - c["in"]) * SR)
        sp = 20 * np.log10(np.sqrt(np.mean(speech[a:b] ** 2)) + 1e-9)
        mu = 20 * np.log10(np.sqrt(np.mean(music[a:b] ** 2)) + 1e-9)
        print(
            f"  {c.get('id', c['kind']):>5} at {c['at']:6.2f}s  speech {sp:6.1f} dB  "
            f"music {mu:6.1f} dB  gap {sp - mu:5.1f} dB"
        )
    raw = AUD / "mix_raw.wav"
    wavfile.write(raw, SR, mix.astype(np.float32))

    # two-pass loudnorm to -14 LUFS / -2 dBTP (headroom for the AAC encode)
    first = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", str(raw), "-af",
         "loudnorm=I=-14:TP=-2:LRA=11:print_format=json", "-f", "null", "-"],
        capture_output=True, text=True, check=True,
    ).stderr  # fmt: skip
    m = json.loads(first[first.rindex("{") : first.rindex("}") + 1])
    flt = (
        "loudnorm=I=-14:TP=-2:LRA=11:linear=true:"
        f"measured_I={m['input_i']}:measured_TP={m['input_tp']}:measured_LRA={m['input_lra']}:"
        f"measured_thresh={m['input_thresh']}:offset={m['target_offset']}"
    )
    out = AUD / "mix.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(raw), "-af", flt, "-ar", str(SR),
                    "-c:a", "pcm_s16le", str(out)], check=True)  # fmt: skip
    meas = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", str(out),
         "-af", "ebur128=peak=true", "-f", "null", "-"],  # fmt: skip
        capture_output=True, text=True,
    ).stderr  # fmt: skip
    summary = meas[meas.rindex("Summary:") :]
    print(summary.strip())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
