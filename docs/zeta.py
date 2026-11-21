#!/usr/bin/env python3
"""Render docs/zeta.gif: ζ(½ + it) winding through the origin at every zero.

Left: the path of ζ(½ + it) in the complex plane as t grows. Each time it passes
through 0, that's a nontrivial zero of ζ on the critical line.
Right: the critical strip 0 < Re(s) < 1, where each zero lights up on Re(s) = ½.

Usage: python3 docs/zeta.py   (needs mpmath, matplotlib, numpy and ffmpeg)
"""
import shutil
import subprocess
import tempfile
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import mpmath
import numpy as np
from matplotlib.collections import LineCollection

OUT = Path(__file__).with_name("zeta.gif")
T_MAX = 52.0
FRAMES = 200          # frames while t sweeps 0 → T_MAX
HOLD = 40             # frames held at the end
FPS = 20
SAMPLES = 3000

BG, FG, DIM = "#0d1117", "#e6edf3", "#6e7681"
CYAN, GOLD, MAGENTA = "#39c5cf", "#f2cc60", "#db61a2"

ts = np.linspace(0, T_MAX, SAMPLES)
zs = np.array([complex(mpmath.zeta(mpmath.mpc(0.5, t))) for t in ts])
zeros = [float(mpmath.zetazero(n).imag) for n in range(1, 40)]
zeros = [z for z in zeros if z <= T_MAX]
LABEL_Y = []  # keep zero labels at least 2 apart
for z in zeros:
    LABEL_Y.append(max(z, LABEL_Y[-1] + 2.0) if LABEL_Y else z)

plt.rcParams.update({"font.family": "DejaVu Sans", "text.color": FG})
fig = plt.figure(figsize=(10, 5), dpi=90, facecolor=BG)
ax = fig.add_axes((0.04, 0.08, 0.62, 0.84), facecolor=BG)
sx = fig.add_axes((0.74, 0.08, 0.22, 0.84), facecolor=BG)


def frame(t_now: float, path: Path):
    for a in (ax, sx):
        a.clear()
        a.set_facecolor(BG)
        for s in a.spines.values():
            s.set_visible(False)
        a.set_xticks([])
        a.set_yticks([])

    # -- complex plane -------------------------------------------------------
    ax.set_xlim(-2.7, 4.0)
    ax.set_ylim(-2.5, 2.5)
    ax.set_aspect("equal")
    ax.axhline(0, color="#21262d", lw=1, zorder=0)
    ax.axvline(0, color="#21262d", lw=1, zorder=0)
    n = int(np.searchsorted(ts, t_now))
    if n > 1:
        pts = np.column_stack([zs[:n].real, zs[:n].imag])
        segs = np.stack([pts[:-1], pts[1:]], axis=1)
        age = np.linspace(0, 1, len(segs)) ** 2       # older segments fade out
        colors = np.zeros((len(segs), 4))
        colors[:, :3] = matplotlib.colors.to_rgb(CYAN)
        colors[:, 3] = 0.12 + 0.88 * age
        ax.add_collection(LineCollection(segs, colors=colors, linewidths=1.8, capstyle="round"))
        cur = zs[n - 1]
        ax.plot(cur.real, cur.imag, "o", ms=14, color=CYAN, alpha=0.25)
        ax.plot(cur.real, cur.imag, "o", ms=6, color="white")
    # A ring pulses out of the origin right after each zero.
    passed = [z for z in zeros if z <= t_now]
    if passed:
        since = t_now - passed[-1]
        if since < 1.6:
            r = 0.15 + since * 0.9
            ax.add_patch(plt.Circle((0, 0), r, fill=False, lw=2.5, color=GOLD, alpha=max(0, 1 - since / 1.6)))
    ax.plot(0, 0, "+", ms=12, mew=1.5, color=GOLD if passed and t_now - passed[-1] < 0.6 else DIM)
    ax.text(-2.6, 2.35, "ζ(½ + it)", fontsize=22, fontweight="bold", color=FG, va="top")
    ax.text(-2.6, 1.75, f"t = {t_now:5.2f}", fontsize=13, family="DejaVu Sans Mono", color=CYAN, va="top")
    ax.text(-2.6, -2.4, f"zeros on the line: {len(passed)}", fontsize=13, family="DejaVu Sans Mono",
            color=GOLD, va="bottom")

    # -- critical strip ------------------------------------------------------
    sx.set_xlim(-0.35, 1.35)
    sx.set_ylim(0, T_MAX)
    sx.axvspan(0, 1, color="#161b22", zorder=0)
    sx.axvline(0, color="#30363d", lw=1)
    sx.axvline(1, color="#30363d", lw=1)
    sx.axvline(0.5, color=MAGENTA, lw=1.2, ls=(0, (4, 3)), alpha=0.8)
    sx.plot([0.5, 0.5], [0, t_now], color=MAGENTA, lw=2.5)
    for z, label_y in zip(zeros, LABEL_Y):
        lit = z <= t_now
        sx.plot(0.5, z, "o", ms=9 if lit else 5, color=GOLD if lit else "#30363d", zorder=3)
        if lit:
            sx.plot(0.5, z, "o", ms=18, color=GOLD, alpha=0.18, zorder=2)
            sx.text(0.62, label_y, f"½ + {z:.2f}i", fontsize=8.5, family="DejaVu Sans Mono", color=GOLD, va="center")
    sx.plot(0.5, t_now, "o", ms=6, color="white", zorder=4)
    sx.text(0.5, -1.2, "Re(s) = ½", fontsize=10, color=MAGENTA, ha="center", va="top")
    sx.text(0, T_MAX + 0.8, "0", fontsize=9, color=DIM, ha="center", va="bottom")
    sx.text(1, T_MAX + 0.8, "1", fontsize=9, color=DIM, ha="center", va="bottom")
    fig.savefig(path, facecolor=BG)


def main():
    tmp = Path(tempfile.mkdtemp())
    try:
        times = list(np.linspace(0.05, T_MAX, FRAMES)) + [T_MAX] * HOLD
        for i, t in enumerate(times):
            if i == 0 or t != times[i - 1]:
                frame(t, tmp / f"f{i:04d}.png")
            else:
                shutil.copy(tmp / f"f{i - 1:04d}.png", tmp / f"f{i:04d}.png")
        pattern = str(tmp / "f%04d.png")
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-framerate", str(FPS), "-i", pattern,
                        "-vf", "palettegen=max_colors=128:stats_mode=diff", str(tmp / "pal.png")], check=True)
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-framerate", str(FPS), "-i", pattern,
                        "-i", str(tmp / "pal.png"),
                        "-lavfi", "paletteuse=dither=none:diff_mode=rectangle", "-loop", "0", str(OUT)],
                       check=True)
    finally:
        shutil.rmtree(tmp)
    print(f"wrote {OUT} ({OUT.stat().st_size / 1e6:.2f} MB)")


if __name__ == "__main__":
    main()
