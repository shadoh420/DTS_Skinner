"""Writes static/skinner-sky/SkinnerSkyNoise.png, the Skinner Sky's one texture: three tiling noises, linear data.

R: cloud shapes (fBm, tile = 1 texture). G: billows (inverted cellular noise blended with fine fBm), which erode the
shapes' edges. B: fine fBm, for cirrus streaks, aurora curtains and the moon's maria. Each is made in the frequency
domain or on a wrapped grid, so every channel tiles. Seeded, so a rerun writes the same file.
"""
import sys
from pathlib import Path

import numpy as np
from PIL import Image

SIZE = 512


def fbm(rng, slope, low, high=SIZE // 2):
    """Noise whose amplitude falls as frequency^-slope between low and high cycles per tile; tiles by construction."""
    k = np.hypot(*np.meshgrid(np.fft.fftfreq(SIZE, 1 / SIZE), np.fft.fftfreq(SIZE, 1 / SIZE)))
    band = np.where((k >= low) & (k <= high), np.maximum(k, 1) ** -slope, 0)
    field = np.fft.ifft2(np.fft.fft2(rng.standard_normal((SIZE, SIZE))) * band).real
    lo, hi = np.percentile(field, (.5, 99.5))
    return np.clip((field - lo) / (hi - lo), 0, 1)


def cells(rng, count):
    """1 - the distance to the nearest of count x count jittered points, on a wrapped grid (Worley F1)."""
    points = (np.stack(np.meshgrid(np.arange(count), np.arange(count)), -1) + rng.random((count, count, 2))) / count
    y, x = np.mgrid[0:SIZE, 0:SIZE] / SIZE
    nearest = np.full((SIZE, SIZE), np.inf)
    for px, py in points.reshape(-1, 2):
        dx, dy = np.abs(x - px), np.abs(y - py)
        nearest = np.minimum(nearest, np.hypot(np.minimum(dx, 1 - dx), np.minimum(dy, 1 - dy)))
    return 1 - np.clip(nearest * count / 1.2, 0, 1)


def main(out):
    rng = np.random.default_rng(1996)
    puffs = .5 * cells(rng, 4) + .3 * cells(rng, 8) + .2 * cells(rng, 16)  # Rounded heaps, the Perlin-Worley mix.
    shapes = .5 * fbm(rng, 2.2, 2, 48) + .5 * (puffs - puffs.min()) / (puffs.max() - puffs.min())
    shapes = np.clip((shapes - np.percentile(shapes, .5)) / (np.percentile(shapes, 99.5) - np.percentile(shapes, .5)), 0, 1)
    billows = .65 * cells(rng, 12) + .35 * fbm(rng, 1.0, 24)
    fine = fbm(rng, 1.2, 6)
    image = np.stack([shapes, billows / billows.max(), fine], -1)
    Image.fromarray((image * 255 + .5).astype(np.uint8), 'RGB').save(out, optimize=True)


if __name__ == '__main__':
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent / 'static' / 'skinner-sky' / 'SkinnerSkyNoise.png')
