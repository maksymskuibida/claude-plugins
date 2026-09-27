#!/usr/bin/env python3
"""Measure a grey card and the image it sits in.

    measure.py work/photos/card.jpg --card X,Y,W,H --out work/measure/card.json
    measure.py work/photos/card.jpg --auto --out ... --preview qa/card-box.jpg
    measure.py work/photos/dish.jpg --auto --white --out ... --preview ...   # no card: a white plate rim

Region is in pixels of the given (working, sRGB) image. --auto looks for the
flattest, least colourful mid-grey patch, which is usually the card and
sometimes a shadowed part of a white plate; with --white it looks for the
flattest bright neutral patch instead (a plate rim, a napkin) for shoots
without a card. Check the --preview before trusting either: the box turns
red when the patch is not flat. White balance ratios are in linear light.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common as C  # noqa: E402


def auto_card(rgb8: np.ndarray, white: bool = False) -> tuple[int, int, int, int]:
    """Best-scoring window: flattest first, then closest to an 18% grey
    (or, with `white`, to a bright plate white around 0.80 encoded).

    Under a colour cast every neutral surface shares the same R/G and B/G,
    so chroma cannot single out the card; it only breaks ties. A matte
    mid-grey table can still win, which is why --preview exists.
    Returns x, y, w, h in the pixels of `rgb8`."""
    h, w = rgb8.shape[:2]
    scale = min(1.0, 800.0 / max(h, w))
    import cv2
    small = cv2.resize(rgb8, (max(8, round(w * scale)), max(8, round(h * scale))), interpolation=cv2.INTER_AREA) if scale < 1 else rgb8
    sh, sw = small.shape[:2]
    win = max(8, min(sh, sw) // 10)
    step = max(2, win // 3)
    lin = C.srgb_to_linear(small.astype(np.float32) / 255.0)
    yenc = small.astype(np.float32) @ C.LUMA_709 / 255.0
    lo, hi, ideal = (0.55, 0.93, 0.80) if white else (0.2, 0.7, 0.42)
    cands = []
    for y0 in range(0, sh - win + 1, step):
        for x0 in range(0, sw - win + 1, step):
            patch_y = yenc[y0:y0 + win, x0:x0 + win]
            m = float(patch_y.mean())
            if m < lo or m > hi:
                continue
            pl = lin[y0:y0 + win, x0:x0 + win].reshape(-1, 3).mean(axis=0)
            chroma = abs(pl[0] / max(pl[1], 1e-6) - 1.0) + abs(pl[2] / max(pl[1], 1e-6) - 1.0)
            cands.append((float(patch_y.std()), abs(m - ideal), float(chroma), x0, y0))
    if not cands:
        C.die("--auto found no flat patch in range; pass --card X,Y,W,H" + ("" if white else " (or try --white for a plate rim)"))

    # log-flatness so a flat card beats textured table by a wide margin;
    # luminance distance from 18% grey in tenths; chroma to reject solid food
    def score(c):
        flat, lum_dist, chroma, _, _ = c
        return math.log(flat + 1e-4) + lum_dist / 0.1 + 2.0 * chroma

    best = min(range(len(cands)), key=lambda i: (score(cands[i]), cands[i][3], cands[i][4]))
    _, _, _, x0, y0 = cands[best]
    inv = 1.0 / scale
    # shrink the box a little so a slightly-off window still sits inside the card
    inset = int(win * 0.15)
    return (round((x0 + inset) * inv), round((y0 + inset) * inv), round((win - 2 * inset) * inv), round((win - 2 * inset) * inv))


def measure(rgb8: np.ndarray, box: tuple[int, int, int, int]) -> dict:
    x, y, w, h = box
    H, W = rgb8.shape[:2]
    if w < 4 or h < 4 or x < 0 or y < 0 or x + w > W or y + h > H:
        C.die(f"card region {box} does not fit inside {W}x{H}")
    patch = rgb8[y:y + h, x:x + w].astype(np.float32) / 255.0
    lin = C.srgb_to_linear(patch).reshape(-1, 3)
    mean_lin = lin.mean(axis=0)
    std_lin = lin.std(axis=0)
    enc = C.linear_to_srgb(mean_lin) * 255.0
    ylin = float(mean_lin @ C.LUMA_709)
    stats = C.image_stats(rgb8)
    return {
        "schema": "dish-media.measure/1",
        "card_region": {"x": x, "y": y, "w": w, "h": h},
        "card_mean_linear": [round(float(v), 6) for v in mean_lin],
        "card_std_linear": [round(float(v), 6) for v in std_lin],
        "card_mean_srgb255": [round(float(v), 2) for v in enc],
        "card_luminance_linear": round(ylin, 6),
        "card_luminance_srgb": round(float(C.linear_to_srgb(np.float32(ylin))), 4),
        "ratios": {"r_over_g": round(float(mean_lin[0] / mean_lin[1]), 5), "b_over_g": round(float(mean_lin[2] / mean_lin[1]), 5)},
        "neutral_error_255": round(float(max(abs(enc[0] - enc[1]), abs(enc[2] - enc[1]))), 2),
        "flatness_cv": round(float(std_lin.mean() / max(mean_lin.mean(), 1e-6)), 4),
        "luminance_percentiles": {k: stats[k] for k in ("y_p1", "y_p5", "y_median", "y_p95", "y_p99")},
        "clipping_pct": {"high_any": stats["clip_high_pct"], "low_all": stats["clip_low_pct"], "high_rgb": stats["clip_high_pct_rgb"]},
        "image": {"width": stats["width"], "height": stats["height"]},
    }


def write_preview(rgb8: np.ndarray, box, path: Path, max_edge: int = 1568, flat: bool = True, label: str = "card") -> None:
    from PIL import Image, ImageDraw
    im = Image.fromarray(rgb8)
    scale = min(1.0, max_edge / max(im.size))
    if scale < 1:
        im = im.resize((round(im.size[0] * scale), round(im.size[1] * scale)), Image.Resampling.LANCZOS)
    d = ImageDraw.Draw(im)
    x, y, w, h = [v * scale for v in box]
    colour = (255, 0, 255) if flat else (255, 40, 40)
    d.rectangle([x, y, x + w, y + h], outline=colour, width=4)
    d.text((x, max(0, y - 16)), label if flat else f"{label}: NOT FLAT", fill=colour)
    C.save_jpeg(path, np.asarray(im), quality=85)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("image")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--card", help="X,Y,W,H in pixels")
    g.add_argument("--auto", action="store_true")
    ap.add_argument("--white", action="store_true", help="with --auto: look for a bright neutral patch (plate rim) instead of 18%% grey")
    ap.add_argument("--out", help="JSON to write (default: stdout)")
    ap.add_argument("--preview", help="JPEG showing the measured box")
    args = ap.parse_args()

    rgb8 = C.load_rgb8(args.image)
    if args.card:
        try:
            box = tuple(int(v) for v in args.card.split(","))
            assert len(box) == 4
        except (ValueError, AssertionError):
            C.die("--card wants four integers: X,Y,W,H")
    else:
        box = auto_card(rgb8, white=args.white)
        C.log(f"auto {'white' if args.white else 'card'} box: {box[0]},{box[1]},{box[2]},{box[3]}")
    m = measure(rgb8, box)
    m["source"] = Path(args.image).name
    if m["flatness_cv"] > 0.08:
        C.log(f"warning: card patch is not flat (cv {m['flatness_cv']}); is the box on the card?")
    text = json.dumps(m, indent=2) + "\n"
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text)
        C.log(f"card mean sRGB {m['card_mean_srgb255']}  R/G {m['ratios']['r_over_g']}  B/G {m['ratios']['b_over_g']}  -> {args.out}")
    else:
        sys.stdout.write(text)
    if args.preview:
        write_preview(rgb8, box, Path(args.preview), flat=m["flatness_cv"] <= 0.08, label="white" if args.white else "card")
    return 0


if __name__ == "__main__":
    sys.exit(main())
