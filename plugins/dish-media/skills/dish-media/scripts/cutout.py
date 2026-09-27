#!/usr/bin/env python3
"""Cut each graded dish out onto the session background with a contact shadow.

    cutout.py --session work/session.json --in out/photos --out out/cutouts \
              --mattes work/mattes --qa qa [--model isnet-general-use]

The matte comes from rembg (segmentation, not generation: it decides which
pixels are dish, it paints nothing). The alpha is eroded and feathered,
composited in linear light over `background`, and a soft shadow is made
from the blurred, offset alpha. Each file gets a confidence score; low
scores go to qa/flags.json as `matte_low_confidence` so they can be
checked on a contact sheet. `--reuse-mattes` recomposites without the model.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common as C  # noqa: E402

DEFAULT_MODEL = "isnet-general-use"
FALLBACK_MODEL = "u2net"


def load_model(name: str):
    try:
        from rembg import new_session
    except ImportError:
        C.die("rembg is not installed; run scripts/setup.sh")
    try:
        return new_session(name), name
    except Exception as e:  # unknown model name or download failure
        if name == FALLBACK_MODEL:
            C.die(f"rembg could not load {name}: {e}")
        C.log(f"warning: rembg could not load {name!r} ({e}); falling back to {FALLBACK_MODEL}")
        return new_session(FALLBACK_MODEL), FALLBACK_MODEL


def matte_for(rgb8: np.ndarray, model) -> np.ndarray:
    from PIL import Image
    from rembg import remove
    mask = remove(Image.fromarray(rgb8), session=model, only_mask=True, post_process_mask=False)
    return np.asarray(mask.convert("L")).astype(np.float32) / 255.0


def confidence(alpha: np.ndarray) -> tuple[float, dict, list[str]]:
    import cv2
    hard = (alpha > 0.5).astype(np.uint8)
    n = alpha.size
    coverage = float(hard.sum()) / n
    soft = float(((alpha > 0.1) & (alpha < 0.9)).sum()) / max(1.0, float(hard.sum()))
    border = np.concatenate([hard[0, :], hard[-1, :], hard[:, 0], hard[:, -1]])
    border_touch = float(border.mean()) if border.size else 0.0
    # holes: background components that do not touch the frame edge
    num, labels, stats, _ = cv2.connectedComponentsWithStats(1 - hard, connectivity=4)
    holes = 0
    h, w = hard.shape
    for i in range(1, num):
        x, y, bw, bh, area = stats[i]
        if x > 0 and y > 0 and x + bw < w and y + bh < h:
            holes += int(area)
    holes_frac = holes / max(1.0, float(hard.sum()))
    # faint ghosts: half-transparent pixels well away from the dish (a card,
    # cutlery, a napkin the model was unsure about) that would print as smudges
    far = cv2.dilate(hard, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (41, 41))) == 0
    ghost = float(((alpha > 0.05) & (alpha < 0.5) & far).sum()) / max(1.0, float(hard.sum()))
    reasons = []
    score = 1.0
    if coverage < 0.15 or coverage > 0.85:
        score -= 0.35
        reasons.append(f"coverage {coverage:.2f} (expected 0.15-0.85)")
    if soft > 0.10:
        score -= min(0.35, (soft - 0.10) * 3.0)
        reasons.append(f"soft edge area {soft:.2f} of subject (glass, steam, thin herbs?)")
    if holes_frac > 0.005:
        score -= min(0.25, holes_frac * 20.0)
        reasons.append(f"holes {holes_frac:.3f} of subject")
    if border_touch > 0.01:
        score -= min(0.25, border_touch * 5.0)
        reasons.append(f"subject touches the frame edge ({border_touch:.2f} of border)")
    if ghost > 0.01:
        score -= min(0.3, ghost * 10.0)
        reasons.append(f"faint ghosts away from the dish ({ghost:.3f} of subject area): card, cutlery, napkin?")
    details = {"coverage": round(coverage, 4), "soft_edge": round(soft, 4), "holes": round(holes_frac, 4),
               "border_touch": round(border_touch, 4), "ghost": round(ghost, 4)}
    return round(max(0.0, min(1.0, score)), 3), details, reasons


def composite(rgb8: np.ndarray, alpha: np.ndarray, p: dict, erode_px: float, feather: float) -> np.ndarray:
    import cv2
    bg = np.asarray(C.parse_colour(p["background"]), dtype=np.float32) / 255.0
    bg_lin = C.srgb_to_linear(bg)
    fg_lin = C.srgb_to_linear(rgb8.astype(np.float32) / 255.0)
    h, w = alpha.shape
    sh = p["shadow"] or {}
    blur = float(sh.get("blur", 40)) * (max(h, w) / 1600.0)      # scale with image size
    offset = float(sh.get("offset", 14)) * (max(h, w) / 1600.0)
    opacity = float(sh.get("opacity", 0.35))
    shadow = alpha.copy()
    if blur > 0:
        shadow = cv2.GaussianBlur(shadow, (0, 0), blur)
    if offset:
        m = np.float32([[1, 0, 0], [0, 1, offset]])
        shadow = cv2.warpAffine(shadow, m, (w, h), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=0)
    shadow = np.clip(shadow * opacity, 0.0, 1.0)
    a = alpha
    if erode_px > 0:
        k = int(round(erode_px)) * 2 + 1
        a = cv2.erode(a, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k)))
    if feather > 0:
        a = cv2.GaussianBlur(a, (0, 0), feather)
    a = np.clip(a, 0.0, 1.0)[..., None]
    back = bg_lin[None, None, :] * (1.0 - shadow[..., None])
    out = fg_lin * a + back * (1.0 - a)
    return np.clip(np.rint(C.linear_to_srgb(out) * 255.0), 0, 255).astype(np.uint8)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--session", required=True)
    ap.add_argument("--in", dest="inp", required=True, help="graded JPEG folder or one file")
    ap.add_argument("--out", required=True)
    ap.add_argument("--mattes", help="folder to keep the alpha PNGs (needed for --reuse-mattes)")
    ap.add_argument("--qa", help="qa folder for flags.json and cutout_stats.json")
    ap.add_argument("--model", default=DEFAULT_MODEL, help=f"rembg model (default {DEFAULT_MODEL}, falls back to {FALLBACK_MODEL})")
    ap.add_argument("--background", help="#RRGGBB, overrides the session")
    ap.add_argument("--erode", type=float, default=1.0, help="pixels to shrink the matte (kills the fringe)")
    ap.add_argument("--feather", type=float, default=1.5, help="Gaussian sigma on the matte edge")
    ap.add_argument("--min-confidence", type=float, default=0.8, help="flag below this; any one full-strength defect drops the score under it")
    ap.add_argument("--reuse-mattes", action="store_true", help="read mattes from --mattes instead of running the model")
    args = ap.parse_args()

    session = C.load_session(args.session)
    if args.background:
        session["background"] = "#" + args.background.lstrip("#").upper()
    files = C.list_images(args.inp)
    if not files:
        C.die(f"no images in {args.inp}")
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    mattes_dir = Path(args.mattes) if args.mattes else None
    if mattes_dir:
        mattes_dir.mkdir(parents=True, exist_ok=True)
    if args.reuse_mattes and not mattes_dir:
        C.die("--reuse-mattes needs --mattes")

    model, model_name = (None, "reused") if args.reuse_mattes else load_model(args.model)
    flags, stats = [], {}
    for src in files:
        p = C.params_for(session, src.name)
        if not p.get("background"):
            C.die(f"no background colour: set one with calibrate.py --background '#F6F4EF' (or --background here)")
        rgb8 = C.load_rgb8(src)
        matte_path = mattes_dir / f"{src.stem}.png" if mattes_dir else None
        if args.reuse_mattes:
            if not matte_path.is_file():
                C.die(f"no matte for {src.name} in {mattes_dir}")
            alpha = np.asarray(__import__("PIL.Image", fromlist=["Image"]).open(matte_path).convert("L")).astype(np.float32) / 255.0
        else:
            alpha = matte_for(rgb8, model)
            if matte_path:
                C.save_png(matte_path, np.clip(np.rint(alpha * 255.0), 0, 255).astype(np.uint8))
        if alpha.shape != rgb8.shape[:2]:
            import cv2
            alpha = cv2.resize(alpha, (rgb8.shape[1], rgb8.shape[0]), interpolation=cv2.INTER_LINEAR)
        score, details, reasons = confidence(alpha)
        out = composite(rgb8, alpha, p, args.erode, args.feather)
        dst = out_dir / f"{src.stem}.jpg"
        C.save_jpeg(dst, out)
        stats[src.stem] = {"confidence": score, "model": model_name, "background": p["background"], **details}
        mark = "" if score >= args.min_confidence else "  <-- check"
        C.log(f"  {src.name}: confidence {score:.2f}{mark}" + (f" ({'; '.join(reasons)})" if reasons else ""))
        if score < args.min_confidence:
            flags.append({"file": dst.name, "code": "matte_low_confidence", "value": score,
                          "detail": "; ".join(reasons) or "low score",
                          "hint": "look at it on the cutout contact sheet; re-run with --erode/--feather, or deliver the plain graded photo instead"})
        if details["border_touch"] > 0.01:
            flags.append({"file": dst.name, "code": "subject_cut", "value": details["border_touch"],
                          "detail": f"the dish touches the frame edge on {details['border_touch']:.0%} of the border: the crop cuts the plate or tray",
                          "hint": "widen the crop for this file (crop_scale up, or a larger crop_box) so the whole vessel sits inside with a margin, then re-grade and re-cut"})
    if args.qa:
        C.update_stats(Path(args.qa) / "cutout_stats.json", stats)
        C.write_flags(args.qa, "cutout", [f.name for f in files], flags)
    C.log(f"cut out {len(files)} file(s) with {model_name} -> {out_dir}; {len(flags)} flagged")
    return 0


if __name__ == "__main__":
    sys.exit(main())
