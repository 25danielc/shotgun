"""Speech islands (> THRESH dB, gaps >= MIN_GAP s) in a mono 48 kHz wav between A and B."""

import sys
import wave

import numpy as np

f, a, b = sys.argv[1], float(sys.argv[2]), float(sys.argv[3])
th = float(sys.argv[4]) if len(sys.argv) > 4 else -42
w = wave.open(f)
sr = w.getframerate()
x = np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(float) / 32768
hop = int(0.01 * sr)
t = np.arange(int(a * 100), int(b * 100)) / 100
e = np.array(
    [20 * np.log10(np.sqrt(np.mean(x[int(s * sr) : int(s * sr) + hop] ** 2)) + 1e-9) for s in t]
)
on = e > th
segs = []
start = None
last = None
for ti, o in zip(t, on, strict=True):
    if o:
        if start is None:
            start = ti
        elif ti - last > 0.18:
            segs.append((start, last + 0.01))
            start = ti
        last = ti
if start is not None:
    segs.append((start, last + 0.01))
print(" ".join(f"[{s:.2f}-{e_:.2f}]" for s, e_ in segs))
