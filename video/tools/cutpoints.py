"""Snap rough speech cut points to the quietest 20 ms nearby (prints refined in/out)."""

import sys
import wave

import numpy as np


def load(p):
    w = wave.open(p)
    sr = w.getframerate()
    x = np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(float) / 32768
    return x, sr


def quiet(x, sr, t, span=0.25, win=0.02):
    best, bt = 1e9, t
    for c in np.arange(t - span, t + span, 0.005):
        a = int(c * sr)
        b = a + int(win * sr)
        if a < 0 or b > len(x):
            continue
        e = float(np.sqrt(np.mean(x[a:b] ** 2)))
        if e < best:
            best, bt = e, c
    return round(bt + win / 2, 3), 20 * np.log10(best + 1e-9)


f = sys.argv[1]
x, sr = load(f)
for pair in sys.argv[2:]:
    a, b = map(float, pair.split(":"))
    (qa, ea), (qb, eb) = quiet(x, sr, a), quiet(x, sr, b)
    print(f"{pair} -> {qa:.3f}:{qb:.3f}  ({ea:.0f} dB / {eb:.0f} dB)")
