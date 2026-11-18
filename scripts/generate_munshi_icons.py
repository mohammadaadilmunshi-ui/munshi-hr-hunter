#!/usr/bin/env python3
"""Derive PWA and theme-aware favicons from the exact approved MUNSHI crown PNG.

Preserves the crown alpha silhouette and 3D facets; only adjusts tonal color for
dark display conditions. No generic icon glyphs or external image services.
"""
from __future__ import annotations
import argparse
from pathlib import Path
from PIL import Image, ImageDraw, ImageFilter
import numpy as np


def crown_for_dark(original: Image.Image) -> Image.Image:
    source = np.asarray(original.convert("RGBA"), dtype=np.float32)
    rgb = source[:, :, :3]
    luminosity = (rgb[:, :, 0] * .2126 + rgb[:, :, 1] * .7152 + rgb[:, :, 2] * .0722)
    # Map the original bevel light/dark structure into a brighter, legible
    # champagne-platinum range; leave every source alpha pixel unchanged.
    l = 0.25 + 0.75 * (luminosity / 255.0) ** .75
    out = np.empty_like(source)
    out[:, :, 0] = 131 + 123 * l
    out[:, :, 1] = 115 + 132 * l
    out[:, :, 2] = 99 + 144 * l
    out[:, :, 3] = source[:, :, 3]
    return Image.fromarray(np.uint8(np.clip(out, 0, 255)))


def fit_crown(crown: Image.Image, size: int, fraction: float) -> Image.Image:
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    # Crop excess transparent margins, then use fixed scale to maintain geometry.
    box = crown.getchannel("A").getbbox()
    assert box is not None
    cut = crown.crop(box)
    target = int(size * fraction)
    cut.thumbnail((target, target), Image.Resampling.LANCZOS)
    canvas.alpha_composite(cut, ((size - cut.width) // 2, (size - cut.height) // 2))
    return canvas


def app_tile(crown: Image.Image, size: int, fraction: float = .71) -> Image.Image:
    # Retina supersampling prevents jagged edges at 180x180 and 192x192.
    hi = max(1024, size * 3)
    bg = Image.new("RGBA", (hi, hi), (31, 32, 37, 255))
    # The restrained halo sits BEHIND the exact crown rather than replacing it.
    halo = Image.new("RGBA", (hi, hi), (0, 0, 0, 0))
    draw = ImageDraw.Draw(halo)
    side = int(hi * .57)
    rect = ((hi - side) // 2, (hi - side) // 2, (hi + side) // 2, (hi + side) // 2)
    draw.ellipse(rect, fill=(226, 194, 145, 104))
    halo = halo.filter(ImageFilter.GaussianBlur(radius=hi * .105))
    bg.alpha_composite(halo)
    bg.alpha_composite(fit_crown(crown, hi, fraction))
    return bg.resize((size, size), Image.Resampling.LANCZOS).convert("RGB")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    source = Image.open(args.source).convert("RGBA")
    dark = crown_for_dark(source)

    for name, crown in [("favicon-light.png", source), ("favicon-dark.png", dark)]:
        fit_crown(crown, 256, .94).save(args.output / name, optimize=True)

    # ICO fallback retains a graphite tile so contrast is dependable in both
    # browser tab appearances when media queries are unsupported.
    ico = app_tile(dark, 256, .84)
    ico.save(args.output / "favicon.ico", format="ICO", sizes=[(16, 16), (32, 32), (48, 48), (64, 64)])

    for name, px, coverage in [
        ("apple-touch-icon.png", 180, .72),
        ("icon-192.png", 192, .72),
        ("icon-512.png", 512, .72),
        ("icon-maskable-512.png", 512, .64),
    ]:
        app_tile(dark, px, coverage).save(args.output / name, optimize=True)
    print("Source preserved:", args.source, "size", source.size)
    for item in sorted(args.output.iterdir()):
        print(item.name, item.stat().st_size, "bytes")


if __name__ == "__main__":
    main()
