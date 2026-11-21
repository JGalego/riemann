#!/usr/bin/env python3
"""Render docs/domain-coloring.gif: a domain coloring of the Riemann ξ function, spinning.

ξ(s) = ½ s(s-1) π^(-s/2) Γ(s/2) ζ(s) has exactly the nontrivial zeros of ζ and
is symmetric about the critical line: ξ(s) = ξ(1-s). Each point s = σ + it is
colored by the phase of ξ(s) (hue) and banded by log|ξ(s)| (light contour
rings; the overall e^(-πt/4) decay is divided out so the bands stay even).
Zeros are the pinwheels where every hue meets. Rotating the hue and sliding the
bands makes them spin and the rings flow into them. Every zero in view sits on
the horizontal mirror line of the picture, Re(s) = ½.

Layout: t (imaginary part) runs left to right over [0, 60]; σ (real part) runs
bottom to top over [-1.5, 2.5], so the critical strip is the middle half.

Usage: python3 docs/domain_coloring.py   (needs mpmath, numpy, matplotlib, ffmpeg)
"""
import shutil
import subprocess
import tempfile
from multiprocessing import Pool
from pathlib import Path

import mpmath
import numpy as np
from matplotlib.colors import LinearSegmentedColormap
from PIL import Image

OUT = Path(__file__).with_name("domain-coloring.gif")
W, H = 900, 240
T0, T1 = 0.0, 60.0
S0, S1 = -1.5, 2.5
FRAMES, FPS = 30, 12

# Light, cyclic, with a red sector.
CMAP = LinearSegmentedColormap.from_list("pastel", [
    "#e5484d", "#ff9478", "#ffd98a", "#b8e8c4", "#8fcdf2", "#b9a5ee", "#f08aa6", "#e5484d"])


def xi(s):
    """ξ(s) times e^(πt/4): a positive factor, so the phase is ξ's own."""
    v = 0.5 * s * (s - 1) * mpmath.pi ** (-s / 2) * mpmath.gamma(s / 2) * mpmath.zeta(s)
    return v * mpmath.exp(mpmath.pi * s.imag / 4)


def row(sigma: float) -> np.ndarray:
    mpmath.mp.dps = 15
    return np.array([complex(xi(mpmath.mpc(sigma, t))) for t in np.linspace(T0, T1, W)])


def grid() -> np.ndarray:
    cache = Path(tempfile.gettempdir()) / f"xi-grid-{W}x{H}-{T0}-{T1}-{S0}-{S1}.npy"
    if cache.exists():
        return np.load(cache)
    sigmas = np.linspace(S1, S0, H)  # top row is the largest σ
    with Pool() as pool:
        z = np.array(pool.map(row, sigmas))
    np.save(cache, z)
    return z


def frame(z: np.ndarray, k: int) -> Image.Image:
    phase = (np.angle(z) / (2 * np.pi) + k / FRAMES) % 1.0
    rgb = CMAP(phase)[..., :3]
    with np.errstate(divide="ignore"):
        bands = (np.log2(np.abs(z) + 1e-300) - k / FRAMES) % 1.0
    shade = 0.80 + 0.20 * bands               # soft sawtooth rings
    rgb = 1 - (1 - rgb) * shade[..., None]    # darken toward the ring edges, stay light
    return Image.fromarray((np.clip(rgb, 0, 1) * 255).astype(np.uint8))


def main():
    z = grid()
    tmp = Path(tempfile.mkdtemp())
    try:
        for k in range(FRAMES):
            frame(z, k).save(tmp / f"f{k:03d}.png")
        pattern = str(tmp / "f%03d.png")
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-framerate", str(FPS), "-i", pattern,
                        "-vf", "palettegen=max_colors=256", str(tmp / "pal.png")], check=True)
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-framerate", str(FPS), "-i", pattern,
                        "-i", str(tmp / "pal.png"), "-lavfi", "paletteuse=dither=none",
                        "-loop", "0", str(OUT)], check=True)
    finally:
        shutil.rmtree(tmp)
    print(f"wrote {OUT} ({OUT.stat().st_size / 1e6:.2f} MB)")


if __name__ == "__main__":
    main()
