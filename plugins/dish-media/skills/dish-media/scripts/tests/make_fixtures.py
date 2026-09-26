#!/usr/bin/env python3
"""Synthetic shoot for the regression suite. No real photos needed.

    make_fixtures.py --out DIR [--dishes 5] [--no-video] [--hdr]

Writes DIR/raw/photos (JPEG, one PNG, one HEIC if `sips` exists, one JPEG
tagged Display P3), DIR/raw/video/turn-001.mov (a patterned disc that
turns exactly once every 8 s at 30 fps for 12 s) and DIR/fixtures.json
with the ground truth (card box in raw pixels, per-dish exposure offsets).

Every dish is rendered with a warm cast (R x1.25, B x0.78) and one stop
under, which is what a badly white-balanced phone shot looks like.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _common as C  # noqa: E402

W, H = 4000, 3000
CARD = [300, 2300, 600, 500]          # x, y, w, h in raw pixels
CARD_LINEAR = 0.18
CAST = np.array([1.25, 1.0, 0.78], dtype=np.float32)
UNDER = 0.5                            # -1 EV
FOODS = [
    (0.50, 0.06, 0.04),   # tomato
    (0.10, 0.35, 0.06),   # herb
    (0.70, 0.50, 0.05),   # yellow
    (0.25, 0.12, 0.05),   # brown
    (0.60, 0.30, 0.20),   # salmon
]


def render_dish(seed: int, ev: float = 0.0, tilt_deg: float = 0.0) -> np.ndarray:
    import cv2
    rng = np.random.default_rng(seed)
    # a table with a little texture, as real tables have (a flat synthetic
    # background would out-score the card in measure.py --auto)
    coarse = rng.normal(0.0, 0.035, (H // 40, W // 40)).astype(np.float32)
    texture = cv2.resize(coarse, (W, H), interpolation=cv2.INTER_CUBIC)
    img = np.repeat((0.22 + texture)[..., None], 3, axis=2).astype(np.float32)
    # plate
    cv2.circle(img, (W // 2, H // 2), 1150, (0.85, 0.85, 0.85), -1, lineType=cv2.LINE_AA)
    cv2.circle(img, (W // 2, H // 2), 1150, (0.70, 0.70, 0.70), 24, lineType=cv2.LINE_AA)
    # food blobs
    n = 4 + int(rng.integers(0, 4))
    for i in range(n):
        col = FOODS[int(rng.integers(0, len(FOODS)))]
        col = tuple(float(np.clip(c * rng.uniform(0.8, 1.2), 0, 1)) for c in col)
        cx = W // 2 + int(rng.integers(-500, 500))
        cy = H // 2 + int(rng.integers(-380, 380))
        ax, ay = int(rng.integers(160, 420)), int(rng.integers(120, 320))
        ang = float(rng.uniform(0, 180))
        cv2.ellipse(img, (cx, cy), (ax, ay), ang, 0, 360, col, -1, lineType=cv2.LINE_AA)
    # a dark fork for the shadows
    cv2.line(img, (W // 2 + 1250, H // 2 - 900), (W // 2 + 1300, H // 2 + 900), (0.03, 0.03, 0.03), 40, lineType=cv2.LINE_AA)
    # grey card
    x, y, w, h = CARD
    img[y:y + h, x:x + w] = CARD_LINEAR
    # texture
    img += rng.normal(0.0, 0.004, img.shape).astype(np.float32)
    if tilt_deg:
        m = cv2.getRotationMatrix2D((W / 2.0, H / 2.0), tilt_deg, 1.0)
        img = cv2.warpAffine(img, m, (W, H), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
    img = img * CAST * np.float32(UNDER * (2.0 ** ev))
    return np.clip(np.rint(C.linear_to_srgb(np.clip(img, 0, 1)) * 255.0), 0, 255).astype(np.uint8)


def save_jpeg_plain(path: Path, rgb8: np.ndarray, icc: bytes | None = None) -> None:
    from PIL import Image
    kw = {"quality": 95, "subsampling": 0}
    if icc:
        kw["icc_profile"] = icc
    Image.fromarray(rgb8).save(str(path), "JPEG", **kw)


def render_disc_pattern(r: int) -> tuple[np.ndarray, np.ndarray]:
    """An asymmetric coloured disc (RGB uint8) and its alpha, both (2r+1)^2."""
    import cv2
    size = 2 * r + 1
    rgb = np.zeros((size, size, 3), dtype=np.uint8)
    alpha = np.zeros((size, size), dtype=np.uint8)
    c = (r, r)
    cv2.circle(alpha, c, r, 255, -1, lineType=cv2.LINE_AA)
    cv2.circle(rgb, c, r, (235, 235, 235), -1, lineType=cv2.LINE_AA)
    cv2.circle(rgb, (r + int(r * 0.45), r), int(r * 0.22), (40, 60, 200), -1, lineType=cv2.LINE_AA)
    cv2.circle(rgb, (r - int(r * 0.3), r - int(r * 0.4)), int(r * 0.15), (200, 40, 40), -1, lineType=cv2.LINE_AA)
    cv2.circle(rgb, (r - int(r * 0.1), r + int(r * 0.55)), int(r * 0.12), (30, 160, 60), -1, lineType=cv2.LINE_AA)
    cv2.ellipse(rgb, (r + int(r * 0.1), r - int(r * 0.6)), (int(r * 0.25), int(r * 0.08)), 30, 0, 360, (230, 180, 30), -1, lineType=cv2.LINE_AA)
    cv2.line(rgb, (r, r), (r + int(r * 0.9), r + int(r * 0.3)), (20, 20, 20), 6, lineType=cv2.LINE_AA)
    return rgb, alpha


def write_video(path: Path, fps: int = 30, seconds: int = 12, period: float = 8.0, size=(960, 540), hdr: bool = False) -> None:
    import cv2
    w, h = size
    r = 200
    disc, alpha = render_disc_pattern(r)
    bg = np.full((h, w, 3), 78, dtype=np.uint8)
    cv2.rectangle(bg, (40, 40), (w - 40, h - 40), (92, 92, 92), 2)
    cmd = ["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}", "-r", str(fps), "-i", "-",
           "-an", "-c:v", "libx264", "-preset", "fast", "-crf", "16", "-g", "30"]
    # tags travel with the frames (setparams); output-only options are dropped by this ffmpeg
    if hdr:
        cmd += ["-vf", "setparams=color_primaries=bt2020:color_trc=arib-std-b67:colorspace=bt2020nc:range=tv", "-pix_fmt", "yuv420p10le"]
    else:
        cmd += ["-vf", "setparams=color_primaries=bt709:color_trc=bt709:colorspace=bt709:range=tv", "-pix_fmt", "yuv420p"]
    cmd += ["-movflags", "+faststart", str(path)]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    n = fps * seconds
    cx, cy = w // 2, h // 2
    for i in range(n):
        t = i / fps
        ang = 360.0 * (t / period)
        m = cv2.getRotationMatrix2D((r, r), ang, 1.0)
        rd = cv2.warpAffine(disc, m, (2 * r + 1, 2 * r + 1), flags=cv2.INTER_LINEAR, borderValue=(0, 0, 0))
        ra = cv2.warpAffine(alpha, m, (2 * r + 1, 2 * r + 1), flags=cv2.INTER_LINEAR, borderValue=0)
        frame = bg.copy()
        y0, x0 = cy - r, cx - r
        a = (ra.astype(np.float32) / 255.0)[..., None]
        roi = frame[y0:y0 + 2 * r + 1, x0:x0 + 2 * r + 1].astype(np.float32)
        frame[y0:y0 + 2 * r + 1, x0:x0 + 2 * r + 1] = np.clip(roi * (1 - a) + rd.astype(np.float32) * a, 0, 255).astype(np.uint8)
        proc.stdin.write(frame.tobytes())
    proc.stdin.close()
    if proc.wait() != 0:
        raise SystemExit("ffmpeg failed writing the fixture clip")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True)
    ap.add_argument("--dishes", type=int, default=5)
    ap.add_argument("--no-video", action="store_true")
    ap.add_argument("--hdr", action="store_true", help="also write an HLG-tagged clip")
    args = ap.parse_args()
    root = Path(args.out)
    photos = root / "raw" / "photos"
    photos.mkdir(parents=True, exist_ok=True)
    truth = {"card_box": CARD, "card_linear": CARD_LINEAR, "cast": CAST.tolist(), "under": UNDER, "width": W, "height": H,
             "dishes": {}, "video": None}
    mac_p3 = Path("/System/Library/ColorSync/Profiles/Display P3.icc")
    for i in range(1, args.dishes + 1):
        ev = 0.5 if i == 4 else 0.0          # dish 4 is the exposure outlier
        tilt = 2.0 if i == 5 else 0.0        # dish 5 needs straightening
        rgb8 = render_dish(seed=1000 + i, ev=ev, tilt_deg=tilt)
        if i == 2:
            name = f"dish-{i:03d}.png"
            from PIL import Image
            Image.fromarray(rgb8).save(str(photos / name), "PNG", optimize=False)
        elif i == 3 and shutil.which("sips"):
            tmp = photos / f"dish-{i:03d}.tmp.jpg"
            save_jpeg_plain(tmp, rgb8, C.srgb_profile_bytes())
            name = f"dish-{i:03d}.HEIC"
            subprocess.run(["sips", "-s", "format", "heic", "-s", "formatOptions", "95", str(tmp), "--out", str(photos / name)],
                           check=True, capture_output=True)
            tmp.unlink()
        elif i == 3:
            name = f"dish-{i:03d}.jpg"
            save_jpeg_plain(photos / name, rgb8)
        elif i == 5 and mac_p3.is_file():
            name = f"dish-{i:03d}.jpg"
            save_jpeg_plain(photos / name, rgb8, mac_p3.read_bytes())   # tagged Display P3 (pixels unchanged)
        else:
            name = f"dish-{i:03d}.jpg"
            save_jpeg_plain(photos / name, rgb8, C.srgb_profile_bytes())
        truth["dishes"][name] = {"ev": ev, "tilt_deg": tilt, "seed": 1000 + i}
        print(f"  {name}", file=sys.stderr)
    if not args.no_video:
        vdir = root / "raw" / "video"
        vdir.mkdir(parents=True, exist_ok=True)
        write_video(vdir / "turn-001.mov")
        truth["video"] = {"file": "turn-001.mov", "fps": 30, "seconds": 12, "period_s": 8.0}
        print("  turn-001.mov", file=sys.stderr)
        if args.hdr:
            write_video(vdir / "hdr-001.mov", seconds=3, hdr=True)
            truth["hdr_video"] = "hdr-001.mov"
            print("  hdr-001.mov", file=sys.stderr)
    (root / "fixtures.json").write_text(json.dumps(truth, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
