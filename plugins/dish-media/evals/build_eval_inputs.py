#!/usr/bin/env python3
"""Assemble the eval input folders from the downloaded Commons files.

    build_eval_inputs.py [--downloads results/data/downloads] [--out results/data/evals]

Makes three read-only shoots:

- session-with-card: the 11 HK phone photos renamed IMG_0002..12 (one of them
  as HEIC), plus IMG_0001.jpg, the same restaurant frame with an 18% grey
  patch pasted in, tinted like the white plate under that light (Commons has
  no grey-card frame; this is the card a photographer would have shot), plus
  two turntable clips re-encoded as phone-like H.264 .mov.
- no-card-mixed: seven photos from four cameras and lights, no card, no video.
- loops-and-hdr: three clips (one ambiguous, one tagged HLG 10-bit as if shot
  in HDR) and a session.json calibrated from the card frame above.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent / "skills" / "dish-media" / "scripts"
sys.path.insert(0, str(SCRIPTS))
import _common as C  # noqa: E402


def card_frame(src: Path, dst: Path) -> dict:
    """Paste an 18% grey patch carrying the plate's tint into a copy of src."""
    from PIL import Image
    with Image.open(src) as im:
        exif = im.info.get("exif")
        rgb8 = np.asarray(im.convert("RGB"))
    lin = C.srgb_to_linear(rgb8.astype(np.float32) / 255.0)
    y = lin @ C.LUMA_709
    chroma = np.abs(lin[..., 0] / np.maximum(lin[..., 1], 1e-4) - 1) + np.abs(lin[..., 2] / np.maximum(lin[..., 1], 1e-4) - 1)
    # the white plate: bright, not clipped, low chroma relative to the rest
    cand = (y > np.percentile(y, 85)) & (y < 0.92) & (chroma < np.percentile(chroma, 40))
    tint = lin[cand].reshape(-1, 3).mean(axis=0)
    tint = tint / (tint @ C.LUMA_709)            # unit luminance, plate's colour
    patch_lin = tint * 0.18
    h, w = rgb8.shape[:2]
    pw, ph = int(w * 0.18), int(w * 0.13)
    x0, y0 = int(w * 0.06), int(h - ph - h * 0.06)
    out = rgb8.copy()
    rng = np.random.default_rng(7)
    patch = np.clip(patch_lin[None, None, :] * (1 + rng.normal(0, 0.01, (ph, pw, 1))), 0, 1)
    out[y0:y0 + ph, x0:x0 + pw] = np.clip(np.rint(C.linear_to_srgb(patch.astype(np.float32)) * 255), 0, 255).astype(np.uint8)
    kw = {"quality": 95, "subsampling": 0}
    if exif:
        kw["exif"] = exif
    Image.fromarray(out).save(str(dst), "JPEG", **kw)
    return {"card_box_raw": [x0, y0, pw, ph], "tint_linear": [round(float(v), 4) for v in tint],
            "tint_ratios": {"r_over_g": round(float(tint[0] / tint[1]), 4), "b_over_g": round(float(tint[2] / tint[1]), 4)}}


def ffmpeg(*args: str) -> None:
    subprocess.run(["ffmpeg", "-v", "error", "-y", *args], check=True)


def phone_clip(src: Path, dst: Path, speed: float, seconds: float, hdr: bool = False) -> None:
    """Loop the render twice, speed it up, cut `seconds`, encode like a phone."""
    tags = ("setparams=color_primaries=bt2020:color_trc=arib-std-b67:colorspace=bt2020nc:range=tv" if hdr
            else "setparams=color_primaries=bt709:color_trc=bt709:colorspace=bt709:range=tv")
    vf = f"setpts=PTS/{speed},fps=30,{tags}"
    enc = ["-c:v", "libx264", "-preset", "fast", "-crf", "18", "-movflags", "+faststart"]
    enc += ["-pix_fmt", "yuv420p10le"] if hdr else ["-pix_fmt", "yuv420p"]
    ffmpeg("-stream_loop", "1", "-i", str(src), "-an", "-vf", vf, "-t", f"{seconds}", *enc, str(dst))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--downloads", default=str(HERE / "results" / "data" / "downloads"))
    ap.add_argument("--out", default=str(HERE / "results" / "data" / "evals"))
    args = ap.parse_args()
    dl, out = Path(args.downloads), Path(args.out)
    if out.exists():
        shutil.rmtree(out)
    truth = {}
    (out / ".inputs.md5").parent.mkdir(parents=True, exist_ok=True)

    # --- session-with-card ------------------------------------------------
    s1 = out / "session-with-card"
    (s1 / "raw" / "photos").mkdir(parents=True)
    (s1 / "raw" / "video").mkdir(parents=True)
    hk = sorted((dl / "hk").glob("N13P_*.jpg"))
    truth["session-with-card"] = {"card": card_frame(hk[0], s1 / "raw" / "photos" / "IMG_0001.jpg"), "photos": {}}
    for i, src in enumerate(hk, start=2):
        if i == 6 and shutil.which("sips"):
            dst = s1 / "raw" / "photos" / f"IMG_{i:04d}.HEIC"
            subprocess.run(["sips", "-s", "format", "heic", "-s", "formatOptions", "95", str(src), "--out", str(dst)], check=True, capture_output=True)
        else:
            dst = s1 / "raw" / "photos" / f"IMG_{i:04d}.jpg"
            shutil.copyfile(src, dst)
        truth["session-with-card"]["photos"][dst.name] = src.name
    phone_clip(dl / "video" / "moon_turntable.webm", s1 / "raw" / "video" / "IMG_0013.mov", speed=10.0, seconds=20)   # 12 s per turn
    phone_clip(dl / "video" / "swift_360.webm", s1 / "raw" / "video" / "IMG_0014.mov", speed=2.0, seconds=20)        # ~10 s per turn
    truth["session-with-card"]["video"] = {"IMG_0013.mov": {"source": "moon_turntable", "period_s": 12.0},
                                           "IMG_0014.mov": {"source": "swift_360", "period_s": 10.0}}

    # --- no-card-mixed ------------------------------------------------------
    s2 = out / "no-card-mixed"
    (s2 / "raw" / "photos").mkdir(parents=True)
    for src in sorted((dl / "mixed").glob("*.jpg")):
        shutil.copyfile(src, s2 / "raw" / "photos" / src.name)
    truth["no-card-mixed"] = {"photos": [p.name for p in sorted((dl / "mixed").glob("*.jpg"))]}

    # --- loops-and-hdr ------------------------------------------------------
    s3 = out / "loops-and-hdr"
    (s3 / "raw" / "photos").mkdir(parents=True)
    (s3 / "raw" / "video").mkdir(parents=True)
    (s3 / "work").mkdir(parents=True)
    phone_clip(dl / "video" / "moon_turntable.webm", s3 / "raw" / "video" / "moon.mov", speed=10.0, seconds=20)
    phone_clip(dl / "video" / "swift_360.webm", s3 / "raw" / "video" / "swift.mov", speed=2.0, seconds=20)
    phone_clip(dl / "video" / "al92_rotation.webm", s3 / "raw" / "video" / "al92.mov", speed=1.0, seconds=16)
    phone_clip(dl / "video" / "moon_turntable.webm", s3 / "raw" / "video" / "moon-hdr.mov", speed=10.0, seconds=20, hdr=True)
    # a real session from the card frame, default look
    tmp = s3 / "work" / "_calib"
    tmp.mkdir()
    subprocess.run([sys.executable, str(SCRIPTS / "ingest.py"), "--in", str(s1 / "raw" / "photos"), "--out", str(tmp / "photos"),
                    "--manifest", str(tmp / "manifest.csv")], check=True, capture_output=True)
    box = truth["session-with-card"]["card"]["card_box_raw"]
    from PIL import Image
    with Image.open(tmp / "photos" / "IMG_0001.jpg") as im:
        scale = im.size[0] / 3060 if im.size[0] < im.size[1] else im.size[0] / 4080
    card = ",".join(str(int(v * scale)) for v in box)
    subprocess.run([sys.executable, str(SCRIPTS / "measure.py"), str(tmp / "photos" / "IMG_0001.jpg"), "--card", card,
                    "--out", str(tmp / "card.json")], check=True, capture_output=True)
    subprocess.run([sys.executable, str(SCRIPTS / "calibrate.py"), "--measure", str(tmp / "card.json"),
                    "--out", str(s3 / "work" / "session.json"), "--note", "from the session-with-card frame"], check=True, capture_output=True)
    shutil.rmtree(tmp)
    truth["loops-and-hdr"] = {"clips": {"moon.mov": 12.0, "swift.mov": 10.0, "al92.mov": None, "moon-hdr.mov": 12.0},
                              "session": json.loads((s3 / "work" / "session.json").read_text())}
    truth["session-with-card"]["card_box_work2400"] = card

    # --- pattaya-menu: 15 single plated dishes, one photographer, no card --------
    s4 = out / "pattaya-menu"
    (s4 / "raw" / "photos").mkdir(parents=True)
    names = []
    for src in sorted((dl / "pattaya").glob("*.jpg")):
        shutil.copyfile(src, s4 / "raw" / "photos" / src.name)
        names.append(src.name)
    truth["pattaya-menu"] = {"photos": names, "source_plate_cut": ["club_sandwich.jpg", "steak_mash.jpg"]}

    # --- loops-real: real rotating food clips re-encoded like phone clips ---------
    s5 = out / "loops-real"
    (s5 / "raw" / "photos").mkdir(parents=True)
    (s5 / "raw" / "video").mkdir(parents=True)
    (s5 / "work").mkdir(parents=True)
    real = dl / "video-real"
    def real_clip(src: Path, dst: Path, hdr: bool = False):
        tags = ("setparams=color_primaries=bt2020:color_trc=arib-std-b67:colorspace=bt2020nc:range=tv" if hdr
                else "setparams=color_primaries=bt709:color_trc=bt709:colorspace=bt709:range=tv")
        enc = ["-c:v", "libx264", "-preset", "fast", "-crf", "18", "-movflags", "+faststart"]
        enc += ["-pix_fmt", "yuv420p10le"] if hdr else ["-pix_fmt", "yuv420p"]
        ffmpeg("-i", str(src), "-an", "-vf", f"fps=30,{tags}", *enc, str(dst))
    clips = {
        "raspberries.mov": ("pexels_raspberries_black_plate.mp4", False),
        "tomato_juice.mov": ("pexels_tomato_juice_plate.mp4", False),
        "baked_dish.mov": ("pexels_baked_dish.mp4", False),
        "fruit_bowl.mov": ("mixkit_rotating_bowl_fruit.mp4", False),
        "chocolate_cake.mov": ("mixkit_rotating_chocolate_cake.mp4", False),
        "cake_stand.mov": ("pexels_spinning_cake_stand.mp4", False),
        "cake_with_baker.mov": ("pexels_rotating_cake_stand.mp4", False),
        "raspberries-hdr.mov": ("pexels_raspberries_black_plate.mp4", True),
    }
    for dst, (src, hdr) in clips.items():
        real_clip(real / src, s5 / "raw" / "video" / dst, hdr=hdr)
    shutil.copyfile(s3 / "work" / "session.json", s5 / "work" / "session.json")
    truth["loops-real"] = {"clips": list(clips), "not_a_dish": ["cake_with_baker.mov"], "portrait": ["chocolate_cake.mov"],
                           "hdr": ["raspberries-hdr.mov"], "partial_rotation": ["raspberries.mov", "tomato_juice.mov", "baked_dish.mov", "chocolate_cake.mov"]}
    (out / "truth.json").write_text(json.dumps(truth, indent=2) + "\n")
    for root, _, files in sorted(__import__("os").walk(out)):
        for f in sorted(files):
            p = Path(root) / f
            print(f"  {p.relative_to(out)}  {p.stat().st_size // 1024} KB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
