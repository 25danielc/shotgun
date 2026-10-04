#!/usr/bin/env python3
"""Shotgun demo video: original music bed and SFX, synthesized in code.

Everything here is generated from oscillators, noise and filters with fixed
seeds, so every run gives the same files. No samples, no recordings.

Run (from video/):
    uv run --no-project --with numpy --with scipy python tools/music.py
Optional:
    --grid work/grid.json   override bpm / downbeat_global_s / sections
                            (used automatically when that file exists)

Writes to video/work/audio/:
    music.wav, music_stems/{pad,pluck,perc,bass,fx}.wav (stems sum to music.wav)
    sfx_key.wav, sfx_key1..3.wav, sfx_tick.wav, sfx_whoosh.wav, sfx_wipe.wav,
    sfx_riser.wav, music_report.json, music_spectrogram.png
"""

from __future__ import annotations

import argparse
import json
import math
import re
import subprocess
import time
from pathlib import Path

import numpy as np
from scipy import ndimage, signal
from scipy.io import wavfile

# ---------------------------------------------------------------- grid ----
SR = 48000
LENGTH_S = 101.0  # global time 0 = first frame of the video
BPM = 105.0
DOWNBEAT_GLOBAL_S = 9.8  # beat 0 (bar 1, downbeat)
INTRO_PAD_START_S = 7.4  # soft pad starts fading in here
INTRO_PAD_DBFS = -30.0  # pad RMS reached at the downbeat
SECTIONS = [  # name, start beat (incl.), end beat (excl.)
    ("B", 0, 8),
    ("C", 8, 16),
    ("D", 16, 24),
    ("E", 24, 39),
    ("F", 39, 76),
    ("G", 76, 100),
    ("H", 100, 114),
    ("I", 114, 121),
    ("J", 121, 135),
    ("K", 135, 146),
    ("L", 146, 148),
    ("M", 148, 156),
]
SEED = 20261004
TARGET_LUFS = -16.0
TRUE_PEAK_CEIL_DB = -1.0
SFX_PEAK_DBFS = -6.0

ROOT = Path(__file__).resolve().parent.parent  # video/
OUT = ROOT / "work" / "audio"
DEFAULT_GRID = ROOT / "work" / "grid.json"

# Harmony: D major, one chord per bar, Dadd9 - A/C# - Bm7 - Gmaj7.
# pad = pad voicing, thin = sparse voicing for the talking section,
# bass = bass root, tones = pluck chord tones (one octave, ascending).
CHORDS = {
    "D": dict(pad=[50, 57, 64, 66, 69], thin=[50, 57, 76], bass=38, tones=[62, 64, 66, 69]),
    "A": dict(pad=[49, 57, 61, 64, 71], thin=[49, 57, 76], bass=37, tones=[61, 64, 69, 71]),
    "Bm": dict(pad=[47, 54, 62, 66, 69], thin=[47, 54, 73], bass=35, tones=[62, 66, 69, 71]),
    "G": dict(pad=[43, 54, 59, 62, 66], thin=[43, 50, 74], bass=31, tones=[62, 66, 67, 71]),
    "Dfinal": dict(
        pad=[50, 57, 64, 66, 69, 76], thin=[50, 57, 76], bass=38, tones=[62, 64, 66, 69]
    ),
}
PROG = ["D", "A", "Bm", "G"]


def mtof(m: float) -> float:
    return 440.0 * 2.0 ** ((m - 69) / 12.0)


class Grid:
    def __init__(self, bpm, downbeat, sections, length_s=LENGTH_S):
        self.bpm = float(bpm)
        self.beat = 60.0 / self.bpm
        self.downbeat = float(downbeat)
        self.sections = [(str(n), float(a), float(b)) for n, a, b in sections]
        self.length = float(length_s)
        self.sec = {n: (a, b) for n, a, b in self.sections}

    def t(self, beat: float) -> float:
        return self.downbeat + beat * self.beat

    def section_at(self, beat: float):
        for n, a, b in self.sections:
            if a <= beat < b:
                return n
        return None

    @property
    def last_beat(self):
        return self.sections[-1][2]


def load_grid(path: Path | None) -> Grid:
    if path is None or not Path(path).exists():
        return Grid(BPM, DOWNBEAT_GLOBAL_S, SECTIONS)
    d = json.loads(Path(path).read_text())
    raw = d.get("sections", SECTIONS)
    secs = []
    if isinstance(raw, dict):
        secs = [(k, v[0], v[1]) for k, v in raw.items()]
    else:
        for s in raw:
            if isinstance(s, dict):
                secs.append(
                    (
                        s["name"],
                        s.get("start_beat", s.get("start")),
                        s.get("end_beat", s.get("end")),
                    )
                )
            else:
                secs.append(tuple(s[:3]))
    secs = [s for s in secs if s[0] not in ("INTRO", "A")]
    secs.sort(key=lambda s: s[1])
    return Grid(
        d.get("bpm", BPM),
        d.get("downbeat_global_s", DOWNBEAT_GLOBAL_S),
        secs,
        d.get("length_s", LENGTH_S),
    )


# ----------------------------------------------------------- helpers ----
def pan_gains(pan: float):
    a = (pan + 1.0) * math.pi / 4.0
    return math.cos(a), math.sin(a)


def add(buf: np.ndarray, t0: float, sig: np.ndarray, pan: float = 0.0):
    i0 = int(round(t0 * SR))
    if sig.ndim == 1:
        gl, gr = pan_gains(pan)
        sig = np.stack([sig * gl, sig * gr], 1)
    if i0 < 0:
        sig = sig[-i0:]
        i0 = 0
    i1 = min(len(buf), i0 + len(sig))
    if i1 > i0:
        buf[i0:i1] += sig[: i1 - i0]


def tvec(dur: float) -> np.ndarray:
    return np.arange(int(dur * SR)) / SR


def bandnoise(rng, n, lo, hi, order=2):
    x = rng.standard_normal(n)
    sos = signal.butter(order, [lo, hi], btype="band", fs=SR, output="sos")
    return signal.sosfilt(sos, x)


def lowpass(x, fc, order=2, axis=0):
    sos = signal.butter(order, fc, btype="low", fs=SR, output="sos")
    return signal.sosfilt(sos, x, axis=axis)


def highpass(x, fc, order=2, axis=0):
    sos = signal.butter(order, fc, btype="high", fs=SR, output="sos")
    return signal.sosfilt(sos, x, axis=axis)


def fade_edges(x, fin=0.0005, fout=0.003):
    n = len(x)
    a, b = int(fin * SR), int(fout * SR)
    if a > 0:
        x[:a] *= np.linspace(0, 1, a)[:, None] if x.ndim == 2 else np.linspace(0, 1, a)
    if b > 0:
        r = np.linspace(1, 0, b)
        x[n - b :] *= r[:, None] if x.ndim == 2 else r
    return x


# ------------------------------------------------------- instruments ----
def marimba(f, vel, rng, dur=2.2):
    """Tuned bar: partials ~1, 3.93, 9.2; upper ones die fast; soft mallet."""
    t = tvec(dur)
    lf = math.log2(f)
    tau = float(np.interp(lf, [math.log2(130), math.log2(1600)], [1.0, 0.28]))
    bright = 0.45 + 0.6 * vel
    out = np.zeros_like(t)
    for ratio, amp, tdiv in (
        (1.0, 1.0, 1.0),
        (3.93, 0.30 * bright, 4.5),
        (9.2, 0.09 * bright, 14.0),
        (2.0, 0.035, 2.0),
    ):
        fr = f * ratio
        if fr > 16000:
            continue
        out += amp * np.sin(2 * np.pi * fr * t) * np.exp(-t / (tau / tdiv))
    out *= 1.0 - np.exp(-t / 0.0012)
    nm = int(0.006 * SR)
    mallet = bandnoise(rng, nm, 600, min(4500, 6 * f)) * np.exp(-np.arange(nm) / SR / 0.0015)
    out[:nm] += 0.10 * bright * mallet / (np.std(mallet) + 1e-9) * 0.25
    # resonator tube: a soft, slightly delayed bloom of the fundamental
    out += (
        0.18 * np.sin(2 * np.pi * f * t + 0.6) * (1 - np.exp(-t / 0.02)) * np.exp(-t / (tau * 1.3))
    )
    return out * vel


def kalimba(f, vel, rng, dur=2.4):
    """Tine: sine with a tiny pitch drop, an inharmonic partial ~5.9x, light buzz."""
    t = tvec(dur)
    tau = float(np.interp(math.log2(f), [math.log2(250), math.log2(2000)], [1.5, 0.55]))
    finst = f * (1 + 0.006 * np.exp(-t / 0.015))
    ph = 2 * np.pi * np.cumsum(finst) / SR
    out = np.sin(ph) * np.exp(-t / tau)
    if f * 5.93 < 16000:
        out += 0.10 * (0.6 + 0.6 * vel) * np.sin(5.93 * ph) * np.exp(-t / 0.06)
    out += 0.05 * np.sin(2 * ph + 0.3) * np.exp(-t / (tau * 0.35))
    out *= 1.0 - np.exp(-t / 0.0018)
    nc = int(0.003 * SR)
    click = bandnoise(rng, nc, 1500, 5000) * np.exp(-np.arange(nc) / SR / 0.0007)
    out[:nc] += 0.04 * vel * click / (np.std(click) + 1e-9)
    return out * vel


def bell(f, vel, dur=5.0):
    t = tvec(dur)
    out = np.zeros_like(t)
    for ratio, amp, tau in (
        (1.0, 1.0, 2.6),
        (2.0, 0.35, 1.6),
        (2.76, 0.30, 1.1),
        (5.40, 0.12, 0.45),
        (8.93, 0.05, 0.2),
    ):
        if f * ratio < 15000:
            out += amp * np.sin(2 * np.pi * f * ratio * t) * np.exp(-t / tau)
    out *= 1 - np.exp(-t / 0.003)
    return out * vel


_TABLES: dict = {}


def pad_table(f0, fc):
    key = (round(f0, 2), round(fc))
    if key in _TABLES:
        return _TABLES[key]
    N = 4096
    nh = max(1, min(60, int(min(15000.0, fc * 4) / f0)))
    hs = np.arange(1, nh + 1)
    saw = 1.0 / hs
    tri = np.where(hs % 2 == 1, ((-1.0) ** ((hs - 1) // 2)) / hs**2, 0.0)
    amp = (0.5 * saw + 0.9 * tri) / np.sqrt(1 + (hs * f0 / fc) ** 4)
    x = np.arange(N) / N
    tab = (amp[:, None] * np.sin(2 * np.pi * hs[:, None] * x[None, :])).sum(0)
    tab /= np.abs(tab).max()
    tab = np.append(tab, tab[0])
    _TABLES[key] = tab
    return tab


def pad_note(midi, dur, rng, attack=0.4, release=1.0, fc=1600.0, width=0.75):
    """Three detuned voices (saw+triangle wavetable, gentle low-pass), slow chorus."""
    f0 = mtof(midi)
    tab = pad_table(f0, fc)
    N = len(tab) - 1
    t = tvec(dur + release)
    n = len(t)
    env = np.ones(n)
    na = max(1, int(attack * SR))
    env[:na] = 0.5 - 0.5 * np.cos(np.pi * np.arange(na) / na)
    nr0 = int(dur * SR)
    nr = n - nr0
    if nr > 0:
        env[nr0:] *= 0.5 + 0.5 * np.cos(np.pi * np.arange(nr) / nr)
    out = np.zeros((n, 2))
    for c, pan in ((-7.0, -width), (0.0, 0.0), (6.0, width)):
        rate = rng.uniform(0.18, 0.45)
        lfo = 2.5 * np.sin(2 * np.pi * rate * t + rng.uniform(0, 2 * np.pi))
        f = f0 * 2.0 ** ((c + lfo) / 1200.0)
        phase = (rng.uniform() + np.cumsum(f) / SR) % 1.0
        v = np.interp(phase * N, np.arange(N + 1), tab)
        gl, gr = pan_gains(pan)
        out[:, 0] += v * gl
        out[:, 1] += v * gr
    return out * env[:, None] / 3.0


def bass_note(f, dur, vel):
    t = tvec(dur + 0.12)
    n = len(t)
    out = (
        np.sin(2 * np.pi * f * t)
        + 0.30 * np.sin(4 * np.pi * f * t + 0.4)
        + 0.09 * np.sin(6 * np.pi * f * t)
        + 0.03 * np.sin(10 * np.pi * f * t)
    )
    env = (1 - np.exp(-t / 0.008)) * (0.72 + 0.28 * np.exp(-t / 0.25))
    nd = int(dur * SR)
    rel = np.ones(n)
    rel[nd:] = np.exp(-np.arange(n - nd) / SR / 0.03)
    return fade_edges(out * env * rel * vel, 0.0, 0.01)


def kick(vel, rng):
    t = tvec(0.38)
    f = 46.0 + 32.0 * np.exp(-t / 0.035)  # ~78 -> 46 Hz, round
    body = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t / 0.15)
    body *= 1 - np.exp(-t / 0.0015)
    beater = 0.12 * np.sin(2 * np.pi * 190 * t) * np.exp(-t / 0.007)
    nc = int(0.004 * SR)
    click = lowpass(rng.standard_normal(nc), 2500) * np.exp(-np.arange(nc) / SR / 0.001)
    out = body + beater
    out[:nc] += 0.05 * click / (np.std(click) + 1e-9)
    return fade_edges(out * vel, 0.0, 0.02)


def shaker(vel, rng, length=0.11, tau=0.03, attack=0.004, lo=4500, hi=11000):
    """Brushed hat / shaker: band-passed noise, soft attack, short decay."""
    t = tvec(length)
    x = bandnoise(rng, len(t), lo, hi)
    x /= np.std(x) + 1e-9
    env = (1 - np.exp(-t / attack)) * np.exp(-t / tau)
    return fade_edges(x * env * vel, 0.0, 0.01)


def snap(vel, rng):
    t = tvec(0.15)
    x = bandnoise(rng, len(t), 1100, 3200)
    x /= np.std(x) + 1e-9
    out = x * (1 - np.exp(-t / 0.0008)) * np.exp(-t / 0.03)
    out += 0.6 * np.sin(2 * np.pi * 420 * t) * np.exp(-t / 0.012)
    return fade_edges(out * vel, 0.0, 0.01)


def swept_noise(rng, dur, centers, bw_oct, stereo_decor=0.0):
    """Noise shaped by a Gaussian band (in octaves) whose centre follows `centers`
    (callable t->Hz), via STFT masking. Returns (n, 2)."""
    n = int(dur * SR)
    out = np.zeros((n, 2))
    base = rng.standard_normal(n + 2048)
    other = rng.standard_normal(n + 2048)
    for ch in range(2):
        x = base * (1 - stereo_decor) + other * stereo_decor if ch else base
        fr, tt, Z = signal.stft(x, SR, nperseg=1024, noverlap=768)
        c = np.maximum(centers(np.clip(tt, 0, dur)), 20.0)
        fsafe = np.maximum(fr, 1.0)[:, None]
        mask = np.exp(-0.5 * (np.log2(fsafe / c[None, :]) / bw_oct) ** 2)
        _, y = signal.istft(Z * mask, SR, nperseg=1024, noverlap=768)
        out[:, ch] = y[:n]
    return out / (np.std(out) + 1e-9)


# ------------------------------------------------------------ reverb ----
def make_ir(rng, rt60=2.3, dur=2.8, predelay=0.018):
    t = tvec(dur)
    tau = rt60 / 6.91
    irs = []
    for _ in range(2):
        x = rng.standard_normal(len(t))
        bright = lowpass(x, 6500)
        dark = lowpass(x, 1700)
        w = np.exp(-t / 0.35)
        ir = (w * bright + (1 - w) * dark) * np.exp(-t / tau)
        ir *= 1 - np.exp(-t / 0.01)
        nd = int(predelay * SR)
        ir = np.concatenate([np.zeros(nd), ir])[: len(t)]
        for d, g in ((0.011, 0.5), (0.019, 0.35), (0.027, 0.3), (0.041, 0.22)):
            k = int((d + rng.uniform(-0.002, 0.002)) * SR)
            ir[k] += g * rng.choice([-1, 1]) * np.abs(ir).max()
        ir /= np.sqrt((ir**2).sum())
        irs.append(ir)
    return irs


def reverb(x, irs, send):
    mono = highpass(x.mean(1), 220)
    wet = np.stack(
        [signal.oaconvolve(mono, irs[0])[: len(x)], signal.oaconvolve(mono, irs[1])[: len(x)]], 1
    )
    return x + send * wet


# ----------------------------------------------------------- arrange ----
def chord_for_bar(grid: Grid, bar: int) -> str:
    final_bar = int(grid.sec["M"][0] // 4)
    if bar >= final_bar:
        return "Dfinal"
    if bar == final_bar - 1:
        return "A"
    return PROG[bar % 4]


def ext_tones(chord: str, base_shift=0):
    tones = CHORDS[chord]["tones"]
    return [m + base_shift + 12 * o for o in range(3) for m in tones]


# Pluck patterns: (position in beats within bar, index into ext tones, velocity)
PLUCK = {
    "B": [(0, 4, 0.75), (1.5, 6, 0.5), (3, 5, 0.55)],
    "C": [(0, 4, 0.75), (1, 5, 0.45), (1.5, 6, 0.55), (2.5, 7, 0.45), (3.5, 6, 0.4)],
    "D": [
        (0, 4, 0.7),
        (0.5, 6, 0.4),
        (1, 5, 0.5),
        (1.5, 7, 0.4),
        (2.5, 6, 0.45),
        (3, 8, 0.5),
        (3.5, 5, 0.38),
    ],
    "E": [(0, 4, 0.7), (1.5, 6, 0.45), (2.5, 5, 0.45), (3, 7, 0.45)],
    "F": [(0, 8, 0.55), (2.5, 9, 0.4)],
    "I": [
        (0, 4, 0.65),
        (0.5, 6, 0.4),
        (1, 5, 0.45),
        (1.5, 7, 0.4),
        (2.5, 6, 0.42),
        (3, 8, 0.45),
        (3.5, 5, 0.35),
    ],
    "J": [(0, 4, 0.7), (1.5, 6, 0.48), (3, 5, 0.5)],
    "K": [
        (0, 4, 0.65),
        (0.5, 6, 0.4),
        (1, 5, 0.45),
        (1.5, 7, 0.4),
        (2.5, 6, 0.42),
        (3, 8, 0.45),
        (3.5, 5, 0.35),
    ],
}
ARP16 = [0, 1, 2, 3, 4, 3, 2, 1, 2, 3, 4, 5, 6, 5, 4, 3]  # marimba 16ths (G, H)
COUNTER = [(0, 9, 0.5), (1.5, 8, 0.4), (2.5, 10, 0.38), (3, 9, 0.42)]


def render(grid: Grid):
    rng = np.random.default_rng(SEED)
    n = int(grid.length * SR)
    stems = {k: np.zeros((n, 2)) for k in ("pad", "pluck", "perc", "bass", "fx")}
    intro = np.zeros((n, 2))
    B = grid.beat

    def jit(sd=0.003):
        return float(np.clip(rng.normal(0, sd), -0.008, 0.008))

    def hv(v, sd=0.07):
        return float(np.clip(v * (1 + rng.normal(0, sd)), 0.05, 1.2))

    M0 = grid.sec["M"][0]
    L0 = grid.sec["L"][0]
    K0 = grid.sec["K"][0]
    n_bars = int(math.ceil(M0 / 4))

    # ---- pad ----
    pad_level = {
        "B": 0.45,
        "C": 0.52,
        "D": 0.66,
        "E": 0.64,
        "F": 0.5,
        "G": 0.82,
        "H": 0.85,
        "I": 0.7,
        "J": 0.42,
        "K": 0.55,
        "L": 0.8,
    }
    pad_fc = {"F": 1500.0, "G": 3000.0, "H": 3200.0, "L": 3400.0}
    for bar in range(n_bars):
        b0 = bar * 4
        sec = grid.section_at(b0)
        ch = chord_for_bar(grid, bar)
        t0 = grid.t(b0)
        dur = 4 * B + 0.05
        notes = CHORDS[ch]["thin" if sec == "F" else "pad"]
        if sec == "G" or sec == "H":
            notes = notes + [notes[2] + 12]
        lvl = pad_level.get(sec, 0.8)
        # bars that straddle a section edge take the louder level of the two halves
        sec2 = grid.section_at(b0 + 2)
        if sec2 != sec:
            lvl = max(lvl, pad_level.get(sec2, 0.8))
            if sec2 == "F" or sec == "F":
                notes = CHORDS[ch]["thin"]
        fc = pad_fc.get(sec2 if sec2 in ("G",) else sec, 2400.0)
        for m in notes:
            add(stems["pad"], t0 - 0.03, pad_note(m, dur, rng, 0.35, 0.9, fc) * lvl * 0.9)
    # final chord: Dadd9 + 13th colour, long hold then natural release
    t_m = grid.t(M0)
    hold = max(1.0, grid.length - 3.3 - t_m)
    for m in CHORDS["Dfinal"]["pad"] + [78]:
        pn = pad_note(m, hold, rng, 0.25, 2.6, 2800.0)
        pn *= np.exp(-np.arange(len(pn)) / SR / 6.0)[:, None]  # natural ring-down
        add(stems["pad"], t_m - 0.02, pn * 1.1)
    # intro pad (scaled later so it sits at INTRO_PAD_DBFS at the downbeat)
    t_in = INTRO_PAD_START_S
    d_in = grid.downbeat - t_in + 0.25
    for m in CHORDS["D"]["pad"]:
        add(intro, t_in, pad_note(m, d_in, rng, d_in * 0.9, 1.2, 1300.0))
    k = np.arange(n) / SR
    ramp = np.clip((k - t_in) / (grid.downbeat - t_in), 0, 1) ** 1.5
    intro *= np.where(k < grid.downbeat, ramp, 1.0)[:, None]

    # ---- plucks ----
    pluck_scale = {"B": 0.8, "C": 0.85, "F": 0.9, "J": 0.75, "K": 0.85}

    def pluck(instr, beat, midi, vel, pan):
        f = mtof(midi)
        vel *= pluck_scale.get(grid.section_at(beat), 1.0)
        s = marimba(f, hv(vel), rng) if instr == "mar" else kalimba(f, hv(vel), rng)
        add(stems["pluck"], grid.t(beat) + jit(), s, pan)

    for bar in range(n_bars):
        ch = chord_for_bar(grid, bar)
        tones = ext_tones(ch)
        for step in range(16):
            beat = bar * 4 + step / 4
            if beat >= M0:
                break
            sec = grid.section_at(beat)
            pos = step / 4
            if sec in ("G", "H"):
                idx = ARP16[step] + 3
                v = 0.62 if step % 4 == 0 else (0.42 if step % 2 == 0 else 0.27)
                pan = -0.35 + 0.7 * (ARP16[step] / 6)
                pluck("mar", beat, tones[idx], v, pan)
                for p, i, cv in COUNTER:
                    if abs(p - pos) < 1e-6 and (bar % 2 == 0 or p != 2.5):
                        pluck("kal", beat, tones[i] + (0 if sec == "G" else 0), cv, 0.4)
            elif sec == "L":
                i = int(round((beat - L0) * 4))
                pluck("mar", beat, tones[min(4 + i, 11)], 0.35 + 0.05 * i, -0.3 + 0.08 * i)
            elif sec in PLUCK:
                for p, i, v in PLUCK[sec]:
                    if abs(p - pos) < 1e-6:
                        if sec == "K" and beat < K0 + 4:
                            v *= 0.8
                        pan = [-0.4, 0.3, -0.15, 0.45][int(pos * 2) % 4]
                        instr = "mar" if (sec in ("D", "E", "K") and pos in (0.5, 2.5)) else "kal"
                        pluck(instr, beat, tones[i], v, pan)
    # final chord roll on M: marimba + kalimba + bell
    for j, m in enumerate([50, 57, 62, 66, 69, 76]):
        add(
            stems["pluck"],
            t_m + 0.022 * j,
            marimba(mtof(m), 0.7 - 0.05 * j, rng, 3.5),
            -0.4 + 0.16 * j,
        )
    add(stems["pluck"], t_m + 0.15, kalimba(mtof(81), 0.45, rng, 3.5), 0.35)
    add(stems["fx"], t_m, bell(mtof(86), 0.3, 5.5), 0.15)
    add(stems["fx"], grid.t(0), bell(mtof(81), 0.12, 4.0), -0.1)  # first downbeat sparkle

    # ---- bass ----
    for bar in range(n_bars):
        ch = chord_for_bar(grid, bar)
        f = mtof(CHORDS[ch]["bass"])
        for half in (0, 2):
            beat = bar * 4 + half
            sec = grid.section_at(beat)
            if sec in ("C",) and half == 0:
                add(stems["bass"], grid.t(beat), bass_note(f, 4 * B - 0.1, 0.45))
            elif sec in ("D", "E", "F", "I"):
                v = 0.6 if sec != "F" else 0.36
                add(stems["bass"], grid.t(beat), bass_note(f, 2 * B - 0.08, hv(v, 0.04)))
            elif sec == "K":
                if beat >= K0 + 4 or half == 0 and beat >= K0:
                    d = 2 * B - 0.08 if beat >= K0 + 4 else 4 * B - 0.1
                    add(stems["bass"], grid.t(beat), bass_note(f, d, 0.55))
            elif sec in ("G", "H"):
                pat = (
                    [(0, 1, 0.85, 1.4), (1.5, 1, 0.5, 0.4)]
                    if half == 0
                    else [(0, 1, 0.75, 0.9), (1.0, 2, 0.45, 0.4), (1.5, 1, 0.5, 0.4)]
                )
                for p, mult, v, d in pat:
                    add(
                        stems["bass"],
                        grid.t(beat + p) + jit(0.002),
                        bass_note(f * mult, d * B, hv(v, 0.04)),
                    )
            elif sec == "L":
                for p in (0, 1):
                    add(stems["bass"], grid.t(beat + p), bass_note(f, 0.8 * B, 0.6))
    add(stems["bass"], t_m, bass_note(mtof(38), 3.6, 0.55) * np.exp(-tvec(3.72) / 2.5))

    # ---- percussion ----
    def hit(fn, beat, v, pan=0.0, sd=0.003):
        add(stems["perc"], grid.t(beat) + jit(sd), fn(hv(v, 0.1), rng), pan)

    for e in range(int(M0 * 4)):
        beat = e / 4
        sec = grid.section_at(beat)
        pos = beat % 4
        is8 = e % 2 == 0
        on = e % 4 == 0
        hats = sec in ("D", "E", "F", "G", "H", "I") or (sec == "K") or sec == "L"
        hat_v = {
            "D": 0.5,
            "E": 0.5,
            "F": 0.34,
            "G": 0.6,
            "H": 0.6,
            "I": 0.5,
            "K": 0.35 + 0.25 * (beat - K0) / max(1.0, L0 - K0),
            "L": 0.6,
        }.get(sec, 0)
        if hats and (is8 or sec in ("G", "H", "L")):
            if is8:
                v = hat_v * (0.75 if on else 1.0)
            else:
                v = hat_v * 0.45
            pan = 0.25 if (e // 2) % 2 else -0.05
            hit(lambda vv, r: shaker(vv, r), beat, v, pan)
        kick_on = sec in ("D", "E", "F", "G", "H") or (sec in ("K", "L") and beat >= K0 + 4)
        if kick_on and on and (pos in (0, 2) or (sec == "L") or beat == K0 + 4):
            hit(kick, beat, {"G": 0.85, "H": 0.85, "F": 0.5}.get(sec, 0.64), 0.0, 0.0015)
        if sec in ("G", "H") and e % 32 == 30:  # soft ghost kick, 4.5 of bar 2
            hit(kick, beat, 0.35, 0.0, 0.0015)
        snap_on = sec in ("D", "E", "F", "G", "H") or (sec == "K" and beat >= K0 + 4)
        if snap_on and on and pos in (1, 3):
            sv = {"G": 0.32, "H": 0.32}.get(sec, 0.22)
            hit(snap, beat, sv, 0.12)
    # soft cymbal-like swell into M
    sw_d = 2 * B
    sw = swept_noise(rng, sw_d, lambda tt: 3500 + 4500 * (tt / sw_d), 0.9, 0.5)
    sw *= ((tvec(sw_d) / sw_d) ** 3)[:, None]
    sw = fade_edges(sw, 0.0, 0.06)
    add(stems["fx"], grid.t(M0) - sw_d, sw * 0.035)
    # riser energy over the last bar before M
    r_d = grid.t(M0) - grid.t(L0 - 2)
    rs = riser_core(rng, r_d)
    add(stems["fx"], grid.t(L0 - 2), fade_edges(rs, 0.0, 0.07) * 0.09)

    # ---- reverb ----
    irs = make_ir(rng)
    sends = {"pad": 0.55, "pluck": 0.42, "perc": 0.18, "bass": 0.0, "fx": 0.5}
    for name, x in stems.items():
        if sends[name] > 0:
            x = reverb(x, irs, sends[name])
        stems[name] = highpass(x, 28.0)  # no subsonic rumble
    intro = reverb(intro, irs, 0.55)

    # ---- stem gains ----
    gains = {"pad": 0.26, "pluck": 0.46, "perc": 0.95, "bass": 0.46, "fx": 1.0}
    for name in stems:
        stems[name] *= gains[name]
    intro *= gains["pad"]
    return stems, intro


def riser_core(rng, dur):
    """Rising band of noise + a soft gliding tone. Peak at the very end."""
    t = tvec(dur)
    x = swept_noise(rng, dur, lambda tt: 300 * (6000 / 300) ** (tt / dur), 0.6, 0.6)
    f = mtof(62) * 2 ** (2 * t / dur)
    tone = np.sin(2 * np.pi * np.cumsum(f) / SR) + 0.3 * np.sin(2 * np.pi * np.cumsum(f * 1.5) / SR)
    env = (t / dur) ** 2.4
    out = x * 0.7 + 0.25 * tone[:, None]
    out *= env[:, None]
    return fade_edges(out, 0.0, 0.004)


# ------------------------------------------------------------ master ----
def k_weight(x):
    b1, a1 = (
        [1.53512485958697, -2.69169618940638, 1.19839281085285],
        [1.0, -1.69065929318241, 0.73248077421585],
    )
    b2, a2 = [1.0, -2.0, 1.0], [1.0, -1.99004745483398, 0.99007225036621]
    return signal.lfilter(b2, a2, signal.lfilter(b1, a1, x, axis=0), axis=0)


def lufs(x):
    y = k_weight(x) ** 2
    blk, hop = int(0.4 * SR), int(0.1 * SR)
    c = np.concatenate([np.zeros((1, 2)), np.cumsum(y, 0)])
    starts = np.arange(0, len(x) - blk + 1, hop)
    z = ((c[starts + blk] - c[starts]) / blk).sum(1)
    lk = -0.691 + 10 * np.log10(z + 1e-20)
    z1 = z[lk > -70]
    if not len(z1):
        return -100.0
    rel = -0.691 + 10 * np.log10(z1.mean()) - 10
    z2 = z[(lk > -70) & (lk > rel)]
    return -0.691 + 10 * np.log10(z2.mean())


def seg_lufs(x):
    y = k_weight(x) ** 2
    return -0.691 + 10 * np.log10(y.mean(0).sum() + 1e-20)


def true_peak_db(x):
    up = signal.resample_poly(x, 4, 1, axis=0)
    return 20 * np.log10(np.abs(up).max() + 1e-20)


def comp_gain(x, thr_db=-20.0, ratio=1.6, att=0.02, rel=0.25):
    """Gentle RMS bus compressor; returns a gain curve (applied to every stem)."""
    p = (x**2).mean(1)
    win = int(0.03 * SR)
    p = ndimage.uniform_filter1d(p, win)
    lvl = 10 * np.log10(p + 1e-12)
    over = np.maximum(lvl - thr_db, 0)
    gr = -over * (1 - 1 / ratio)  # dB, <= 0
    # attack/release smoothing on a decimated curve (fast enough in Python)
    dec = 48
    g = gr[::dec]
    out = np.empty_like(g)
    a_c = math.exp(-dec / (att * SR))
    r_c = math.exp(-dec / (rel * SR))
    s = 0.0
    for i, v in enumerate(g):
        c_ = a_c if v < s else r_c
        s = c_ * s + (1 - c_) * v
        out[i] = s
    full = np.interp(np.arange(len(x)), np.arange(len(out)) * dec, out)
    return 10 ** (full / 20)


def limiter_gain(x, ceil_db):
    """Look-ahead peak limiter on 4x-oversampled peaks; returns a gain curve."""
    up = np.abs(signal.resample_poly(x, 4, 1, axis=0)).max(1)
    pk = up[: len(x) * 4].reshape(len(x), 4).max(1)
    ceil = 10 ** (ceil_db / 20)
    need = np.minimum(1.0, ceil / np.maximum(pk, 1e-9))
    la = int(0.005 * SR)
    g = ndimage.minimum_filter1d(need, 2 * la + 1)
    g = ndimage.uniform_filter1d(g, la)
    return np.minimum(g, ndimage.minimum_filter1d(need, 3))


def master(stems, intro, grid: Grid):
    names = list(stems)
    total = sum(stems.values())
    g0 = 10 ** ((TARGET_LUFS - lufs(total)) / 20)
    for k_ in names:
        stems[k_] *= g0
    intro *= g0
    total = sum(stems.values())
    cg = comp_gain(total)[:, None]
    for k_ in names:
        stems[k_] *= cg
    # tail fade so the file ends in silence at LENGTH_S
    n = len(total)
    t = np.arange(n) / SR
    fade = np.clip((grid.length - t) / 1.6, 0, 1)
    fade = (0.5 - 0.5 * np.cos(np.pi * fade))[:, None]
    for k_ in names:
        stems[k_] *= fade
    for _ in range(3):
        total = sum(stems.values())
        g = 10 ** ((TARGET_LUFS - lufs(total)) / 20)
        tp = true_peak_db(total * g)
        if tp > TRUE_PEAK_CEIL_DB - 0.3:
            lg = limiter_gain(total * g, TRUE_PEAK_CEIL_DB - 0.4)[:, None]
        else:
            lg = 1.0
        for k_ in names:
            stems[k_] *= g * lg
    # intro pad level: RMS just before the downbeat (negligible for the gated LUFS)
    a, b = int((grid.downbeat - 0.3) * SR), int((grid.downbeat - 0.05) * SR)
    rms_in = np.sqrt((intro[a:b] ** 2).mean())
    intro *= 10 ** (INTRO_PAD_DBFS / 20) / (rms_in + 1e-12)
    stems["pad"] += intro
    total = sum(stems.values())
    return stems, total


# --------------------------------------------------------------- sfx ----
def sfx_key(seed):
    rng = np.random.default_rng(seed)
    t = tvec(0.025)
    n = len(t)
    out = np.zeros(n)
    c1 = bandnoise(rng, n, 1800 + 300 * rng.uniform(), 6500)
    c1 = c1 / (np.std(c1[:200]) + 1e-9) * np.exp(-t / 0.0010)
    out += 0.5 * c1
    fth = 230 + 80 * rng.uniform()
    out += (
        0.9
        * np.sin(2 * np.pi * np.cumsum(fth * (1 + 0.4 * np.exp(-t / 0.002))) / SR)
        * np.exp(-t / 0.0045)
        * (1 - np.exp(-t / 0.0004))
    )
    out += 0.25 * np.sin(2 * np.pi * (1050 + 200 * rng.uniform()) * t) * np.exp(-t / 0.0025)
    d2 = 0.007 + 0.003 * rng.uniform()
    k2 = int(d2 * SR)
    c2 = bandnoise(rng, n - k2, 1500, 5000)
    tt2 = t[: n - k2]
    out[k2:] += 0.22 * c2 / (np.std(c2[:200]) + 1e-9) * np.exp(-tt2 / 0.0012)
    out = lowpass(out, 9000)
    return fade_edges(np.stack([out, out * 0.97], 1), 0.0002, 0.004)


def sfx_tick():
    t = tvec(0.06)
    f = 1760 * (1 + 0.035 * (1 - np.exp(-t / 0.004)))
    ph = 2 * np.pi * np.cumsum(f) / SR
    out = (
        np.sin(ph) * np.exp(-t / 0.016)
        + 0.28 * np.sin(2 * ph) * np.exp(-t / 0.007)
        + 0.12 * np.sin(3 * ph) * np.exp(-t / 0.004)
    )
    out *= 1 - np.exp(-t / 0.0007)
    return fade_edges(np.stack([out, out], 1), 0.0, 0.006)


def sfx_whoosh(rng):
    dur, pk = 0.45, 0.30
    t = tvec(dur)

    def cen(tt):
        return np.where(
            tt < pk,
            350 * (1700 / 350) ** (tt / pk),
            1700 * (800 / 1700) ** ((tt - pk) / (dur - pk)),
        )

    x = swept_noise(rng, dur, cen, 0.85, 0.35)
    env = np.where(t < pk, (t / pk) ** 2.2, np.exp(-(t - pk) / 0.045))
    x *= env[:, None]
    # pan sweep L -> R
    p = np.clip((t / dur) * 1.4 - 0.7, -0.7, 0.7)
    a = (p + 1) * np.pi / 4
    x[:, 0] *= np.cos(a) * 1.41
    x[:, 1] *= np.sin(a) * 1.41
    return fade_edges(lowpass(x, 7000, axis=0), 0.002, 0.01)


def sfx_wipe(rng):
    dur, pk = 0.30, 0.17
    t = tvec(dur)
    x = swept_noise(rng, dur, lambda tt: 5000 * (9000 / 5000) ** (tt / dur), 0.5, 0.5)
    env = np.where(t < pk, np.sin(0.5 * np.pi * t / pk) ** 2, np.exp(-(t - pk) / 0.035))
    x *= env[:, None]
    p = np.clip((t / dur) * 1.2 - 0.6, -0.6, 0.6)
    a = (p + 1) * np.pi / 4
    x[:, 0] *= np.cos(a) * 1.41
    x[:, 1] *= np.sin(a) * 1.41
    return fade_edges(x, 0.002, 0.01)


def norm_peak(x, db):
    return x * (10 ** (db / 20) / (np.abs(x).max() + 1e-12))


def peak_offset(x):
    e = ndimage.uniform_filter1d((x**2).mean(1), max(1, int(0.002 * SR)))
    return float(np.argmax(e) / SR)


# ---------------------------------------------------------- analysis ----
def write_wav(path: Path, x: np.ndarray):
    path.parent.mkdir(parents=True, exist_ok=True)
    wavfile.write(str(path), SR, x.astype(np.float32))


def band_share(x, lo=None, hi=None):
    m = x.mean(1)
    X = np.abs(np.fft.rfft(m)) ** 2
    f = np.fft.rfftfreq(len(m), 1 / SR)
    tot = X.sum() + 1e-20
    sel = np.ones_like(f, bool)
    if lo is not None:
        sel &= f >= lo
    if hi is not None:
        sel &= f < hi
    return float(X[sel].sum() / tot)


def analyze(total, stems, grid: Grid):
    rows = []
    allsecs = [("INTRO", None, 0.0)] + list(grid.sections)
    print("\nsection  start_s  end_s   RMS_dBFS  LUFS(seg)  >6k%  <120Hz%")
    for name, a, b in allsecs:
        s0 = 0.0 if a is None else grid.t(a)
        s1 = grid.t(b) if name != "M" else grid.length
        seg = total[int(s0 * SR) : int(s1 * SR)]
        rms = 20 * np.log10(np.sqrt((seg**2).mean()) + 1e-12)
        lu = seg_lufs(seg)
        hi = 100 * band_share(seg, lo=6000)
        lo = 100 * band_share(seg, hi=120)
        rows.append(
            dict(
                name=name,
                start_beat=a if a is not None else None,
                end_beat=b,
                start_s=round(s0, 4),
                end_s=round(s1, 4),
                rms_dbfs=round(rms, 2),
                lufs_ungated=round(lu, 2),
                share_above_6k_pct=round(hi, 2),
                share_below_120_pct=round(lo, 2),
            )
        )
        print(f"{name:7s} {s0:7.2f} {s1:7.2f}  {rms:8.2f}  {lu:8.2f}  {hi:5.1f}  {lo:6.1f}")
    gh = total[int(grid.t(grid.sec["G"][0]) * SR) : int(grid.t(grid.sec["H"][1]) * SR)]
    whole = {
        "share_above_6k_pct": round(100 * band_share(total, lo=6000), 2),
        "share_below_120_pct": round(100 * band_share(total, hi=120), 2),
        "GH_share_above_6k_pct": round(100 * band_share(gh, lo=6000), 2),
        "GH_share_below_120_pct": round(100 * band_share(gh, hi=120), 2),
    }
    print("spectral:", whole)
    # per-bar RMS
    bars = []
    nb = int(math.ceil(grid.last_beat / 4))
    for bar in range(nb):
        s0, s1 = grid.t(bar * 4), min(grid.t(bar * 4 + 4), grid.length)
        seg = total[int(s0 * SR) : int(s1 * SR)]
        bars.append(round(20 * np.log10(np.sqrt((seg**2).mean()) + 1e-12), 1))
    print("per-bar RMS dBFS:", " ".join(f"{v:.0f}" for v in bars))
    # stem RMS in G
    g0, g1 = int(grid.t(grid.sec["G"][0]) * SR), int(grid.t(grid.sec["G"][1]) * SR)
    srms = {
        k: round(20 * np.log10(np.sqrt((v[g0:g1] ** 2).mean()) + 1e-12), 1)
        for k, v in stems.items()
    }
    print("stem RMS in G (dBFS):", srms)
    a, b = int((grid.downbeat - 0.3) * SR), int((grid.downbeat - 0.05) * SR)
    intro_rms = 20 * np.log10(np.sqrt((total[a:b] ** 2).mean()) + 1e-12)
    early = total[: int(INTRO_PAD_START_S * SR)]
    print(
        f"intro pad RMS before downbeat: {intro_rms:.1f} dBFS; "
        f"max |x| before {INTRO_PAD_START_S}s: {np.abs(early).max():.2e}"
    )
    return rows, whole, bars, srms, intro_rms


def ffmpeg_loudness(path: Path):
    r = subprocess.run(
        [
            "ffmpeg",
            "-nostats",
            "-hide_banner",
            "-i",
            str(path),
            "-af",
            "ebur128=peak=true",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
    )
    txt = r.stderr
    summ = txt[txt.rfind("Summary:") :]
    i = re.search(r"I:\s+(-?[\d.]+) LUFS", summ)
    tp = re.search(r"True peak:\s+Peak:\s+(-?[\d.]+) dBFS", summ, re.S)
    lra = re.search(r"LRA:\s+(-?[\d.]+) LU", summ)
    return (
        float(i.group(1)) if i else None,
        float(tp.group(1)) if tp else None,
        float(lra.group(1)) if lra else None,
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--grid", type=Path, default=DEFAULT_GRID)
    ap.add_argument("--no-png", action="store_true")
    args = ap.parse_args()
    t_start = time.time()
    grid = load_grid(args.grid)
    print(
        f"grid: {grid.bpm} BPM, beat {grid.beat:.6f} s, downbeat {grid.downbeat} s, "
        f"{len(grid.sections)} sections, from {'file' if args.grid.exists() else 'constants'}"
    )

    stems, intro = render(grid)
    print(f"rendered in {time.time() - t_start:.1f} s")
    stems, total = master(stems, intro, grid)
    print(
        f"mastered at {time.time() - t_start:.1f} s; internal LUFS {lufs(total):.2f}, "
        f"TP {true_peak_db(total):.2f} dBTP, sample peak "
        f"{20 * np.log10(np.abs(total).max()):.2f} dBFS"
    )

    OUT.mkdir(parents=True, exist_ok=True)
    write_wav(OUT / "music.wav", total)
    for k_, v in stems.items():
        write_wav(OUT / "music_stems" / f"{k_}.wav", v)
    s32 = sum(v.astype(np.float32).astype(np.float64) for v in stems.values())
    sum_err = float(np.abs(s32 - total.astype(np.float32)).max())

    rows, whole, bars, srms, intro_rms = analyze(total, stems, grid)

    # SFX
    sfx = {}
    rng = np.random.default_rng(SEED + 7)
    items = {
        "sfx_key1": sfx_key(SEED + 11),
        "sfx_key2": sfx_key(SEED + 12),
        "sfx_key3": sfx_key(SEED + 13),
        "sfx_tick": sfx_tick(),
        "sfx_whoosh": sfx_whoosh(rng),
        "sfx_wipe": sfx_wipe(rng),
        "sfx_riser": riser_core(rng, 1.1),
    }
    items["sfx_key"] = items["sfx_key1"]
    for name, x in items.items():
        x = norm_peak(x, SFX_PEAK_DBFS)
        write_wav(OUT / f"{name}.wav", x)
        sfx[name] = dict(
            file=f"{name}.wav",
            duration_s=round(len(x) / SR, 4),
            peak_offset_s=round(peak_offset(x), 4),
            sample_peak_dbfs=SFX_PEAK_DBFS,
        )
    print("sfx peak offsets:", {k: v["peak_offset_s"] for k, v in sfx.items()})

    integrated, TP, LRA = ffmpeg_loudness(OUT / "music.wav")
    print(f"ffmpeg ebur128: I {integrated} LUFS, true peak {TP} dBFS, LRA {LRA} LU")
    if not args.no_png:
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-loglevel",
                "error",
                "-i",
                str(OUT / "music.wav"),
                "-lavfi",
                "showspectrumpic=s=1600x600:legend=1",
                str(OUT / "music_spectrogram.png"),
            ],
            check=False,
        )

    report = dict(
        generator=f"video/tools/music.py (synthesized in code, fixed seed {SEED})",
        sample_rate=SR,
        length_s=grid.length,
        bpm=grid.bpm,
        beat_s=round(grid.beat, 6),
        downbeat_global_s=grid.downbeat,
        time_signature="4/4",
        beat_to_global="t = downbeat_global_s + beat * beat_s",
        key="D major; bars loop Dadd9 - A/C# - Bm7 - Gmaj7; bar before M is A; M = Dadd9",
        intro_pad=dict(start_s=INTRO_PAD_START_S, rms_dbfs_at_downbeat=round(intro_rms, 2)),
        sections=rows,
        events=dict(
            first_downbeat_s=round(grid.t(0), 4),
            kick_returns_s=round(grid.t(grid.sec["K"][0] + 4), 4),
            riser_start_s=round(grid.t(grid.sec["L"][0] - 2), 4),
            final_chord_s=round(grid.t(grid.sec["M"][0]), 4),
            silence_by_s=grid.length,
        ),
        sfx=sfx,
        sfx_note=f"all SFX are normalized to {SFX_PEAK_DBFS:.0f} dBFS sample peak; peak_offset_s "
        "is the argmax of a 2 ms energy envelope from the file start",
        loudness=dict(
            ffmpeg_integrated_lufs=integrated,
            ffmpeg_true_peak_dbfs=TP,
            ffmpeg_lra_lu=LRA,
            internal_integrated_lufs=round(lufs(total), 2),
            internal_true_peak_dbtp=round(true_peak_db(total), 2),
        ),
        spectral=whole,
        per_bar_rms_dbfs=bars,
        stem_rms_in_G_dbfs=srms,
        stems=[f"music_stems/{k}.wav" for k in stems],
        stems_sum_max_abs_error=sum_err,
        render_seconds=round(time.time() - t_start, 1),
    )
    (OUT / "music_report.json").write_text(json.dumps(report, indent=2))
    print(f"done in {time.time() - t_start:.1f} s; stems sum error {sum_err:.2e}")


if __name__ == "__main__":
    main()
