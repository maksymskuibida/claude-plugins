#!/usr/bin/env python3
"""Apply session.json to working JPEGs and write the delivery JPEGs.

    grade.py --session work/session.json --in work/photos --out out/photos --qa qa
    grade.py --session work/session.json --in work/photos/dish-0412.jpg --out out/photos --qa qa
    grade.py --session work/session.json --variants work/photos/dish-0412.jpg --out qa/variants

Pipeline per file: sRGB -> linear, white-balance gains, exposure, tone curve
(shadows lift, highlight knee, sigmoid contrast; ratio-preserving on
luminance so hue and chroma are untouched), saturation as OKLab chroma
scaling, back to sRGB, straighten, crop, resize, JPEG q90 with the sRGB
profile embedded and no EXIF. Per-file `overrides` in the session apply.

--variants renders one reference at -0.3 / 0 / +0.3 EV and two contrast
strengths (six files) for the calibration contact sheet.
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


def inscribed_rect(w: int, h: int, angle_deg: float, aspect: float | None) -> tuple[float, float]:
    """Half-sizes (hw, hh) of the largest centred axis-aligned rectangle of
    the given aspect that fits inside a w x h image rotated by angle."""
    a = abs(math.radians(angle_deg)) % (math.pi / 2)
    c, s = math.cos(a), math.sin(a)
    if aspect is None:
        aspect = w / h
    hh = min(w / (2.0 * (aspect * c + s)), h / (2.0 * (aspect * s + c)))
    return aspect * hh, hh


def apply_geometry(img: np.ndarray, p: dict) -> np.ndarray:
    import cv2
    h, w = img.shape[:2]
    angle = float(p.get("straighten_deg") or 0.0)
    aspect = C.parse_ratio(p.get("crop_ratio"))
    if angle:
        m = cv2.getRotationMatrix2D((w / 2.0, h / 2.0), angle, 1.0)
        img = cv2.warpAffine(img, m, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0))
    box = p.get("crop_box")
    if box:                              # [x, y, w, h] as fractions of the frame: exactly this window, its own aspect
        bx, by, bw, bh = [float(v) for v in box]
        x0, y0 = int(round(max(0.0, bx) * w)), int(round(max(0.0, by) * h))
        x1, y1 = int(round(min(1.0, bx + bw) * w)), int(round(min(1.0, by + bh) * h))
        if x1 - x0 < 16 or y1 - y0 < 16:
            C.die(f"crop_box {box} is empty or outside the frame")
        img = img[y0:y1, x0:x1]
        return resize_long_edge(img, int(p.get("output_long_edge") or 1600))
    if aspect is not None and (h > w) != (aspect < 1):
        aspect = 1.0 / aspect          # "4:3" means long:short, whichever way the photo stands
    hw, hh = inscribed_rect(w, h, angle, aspect)
    cs = min(1.0, max(0.3, float(p.get("crop_scale") or 1.0)))
    hw, hh = hw * cs, hh * cs          # < 1 crops away edges (a watermark, a neighbour's plate)
    cx, cy = p.get("crop_center") or [0.5, 0.5]
    # the centre may move only as far as keeps the crop inside the valid area;
    # a straightened image keeps the crop centred (its valid area is not a rectangle)
    cx = min(max(float(cx) * w, hw), w - hw) if angle == 0 else w / 2.0
    cy = min(max(float(cy) * h, hh), h - hh) if angle == 0 else h / 2.0
    x0, y0 = int(round(cx - hw)), int(round(cy - hh))
    x1, y1 = int(round(cx + hw)), int(round(cy + hh))
    img = img[max(0, y0):y1, max(0, x0):x1]
    return resize_long_edge(img, int(p.get("output_long_edge") or 1600))


def resize_long_edge(img: np.ndarray, long_edge: int) -> np.ndarray:
    import cv2
    ch, cw = img.shape[:2]
    scale = long_edge / max(cw, ch)
    if abs(scale - 1.0) > 1e-9:
        new = (max(1, round(cw * scale)), max(1, round(ch * scale)))
        interp = cv2.INTER_AREA if scale < 1 else cv2.INTER_LANCZOS4
        img = cv2.resize(img, new, interpolation=interp)
    return img


def grade_array(rgb8: np.ndarray, p: dict) -> np.ndarray:
    lin = C.srgb_to_linear(rgb8.astype(np.float32) / 255.0)
    lin = C.apply_look_linear(lin, p)
    enc = C.linear_to_srgb(lin)
    enc = apply_geometry(enc, p)
    return np.clip(np.rint(enc * 255.0), 0, 255).astype(np.uint8)


def grade_one(src: Path, dst: Path, p: dict) -> dict:
    rgb8 = C.load_rgb8(src)
    out = grade_array(rgb8, p)
    C.save_jpeg(dst, out)
    st = C.image_stats(out)
    st["source"] = src.name
    st["exposure"] = round(float(p["exposure"]), 6)
    st["overridden"] = bool(p.get("_overridden"))
    return st


def _worker(job):
    src, dst, p = job
    return src.name, grade_one(src, dst, p)


def vessel_check(files, out_dir: Path) -> list[dict]:
    """subject_cut flags: the plate touches the crop edge where it did not in the source.

    Uses rembg on 800 px copies of the graded output and the working file; a
    frame whose source already cut the vessel is not blamed for the crop."""
    try:
        import cutout as CO
    except ImportError:
        return []
    try:
        model, _ = CO.load_model(CO.DEFAULT_MODEL)
    except SystemExit:
        C.log("  vessel check skipped: rembg not available")
        return []
    import cv2
    flags = []

    def small(path: Path):
        a = C.load_rgb8(path)
        s = 800.0 / max(a.shape[:2])
        if s < 1:
            a = cv2.resize(a, (max(8, round(a.shape[1] * s)), max(8, round(a.shape[0] * s))), interpolation=cv2.INTER_AREA)
        return a

    def matte_touch(a) -> float:
        return CO.confidence(CO.matte_for(a, model))[1]["border_touch"]

    def plate_touch(a) -> float:
        """Border contact of the largest bright, low-chroma blob: a white plate the
        segmentation model may leave out (it sees food, not china)."""
        lin = C.srgb_to_linear(a.astype(np.float32) / 255.0)
        y = lin @ C.LUMA_709
        chroma = np.abs(lin[..., 0] / np.maximum(lin[..., 1], 1e-4) - 1) + np.abs(lin[..., 2] / np.maximum(lin[..., 1], 1e-4) - 1)
        mask = ((y > 0.40) & (chroma < 0.35)).astype(np.uint8)
        n, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=4)
        if n < 2:
            return 0.0
        big = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
        if stats[big, cv2.CC_STAT_AREA] < 0.03 * mask.size:
            return 0.0
        comp = labels == big
        border = np.concatenate([comp[0, :], comp[-1, :], comp[:, 0], comp[:, -1]])
        return float(border.mean())
    for src in files:
        dst = out_dir / f"{src.stem}.jpg"
        if not dst.is_file():
            continue
        a_out, a_src = small(dst), small(src)
        m_out, m_src = matte_touch(a_out), matte_touch(a_src)
        p_out, p_src = plate_touch(a_out), plate_touch(a_src)
        by_matte = m_out > 0.01 and m_out > m_src + 0.005
        by_plate = p_out > 0.05 and p_out > p_src + 0.03
        if by_matte or by_plate:
            t_out, t_src = (m_out, m_src) if by_matte else (p_out, p_src)
            flags.append({"file": dst.name, "code": "subject_cut", "value": round(t_out, 3),
                          "detail": f"the {'dish' if by_matte else 'plate'} touches the crop edge on {t_out:.0%} of the border (source: {t_src:.0%}): the crop cuts the vessel",
                          "hint": "widen the crop for this file (crop_scale up, crop_ratio none, or a larger crop_box) and re-grade"})
            C.log(f"  {dst.name}: subject_cut ({t_out:.0%} of the border, source {t_src:.0%})")
    return flags


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--session", required=True)
    ap.add_argument("--in", dest="inp", help="working JPEG folder or one file")
    ap.add_argument("--out", required=True, help="output folder")
    ap.add_argument("--qa", help="qa folder for grade_stats.json and flags.json")
    ap.add_argument("--variants", metavar="REFERENCE", help="render calibration variants of this one file into --out")
    ap.add_argument("--alt-contrast", type=float, help="second contrast strength for --variants (default: session + 2.0)")
    ap.add_argument("--workers", type=int, default=1, help="parallel processes (output is identical either way)")
    ap.add_argument("--clip-high-warn", type=float, default=1.0, help="flag files with more than this %% of pixels at 254+")
    ap.add_argument("--clip-low-warn", type=float, default=2.0, help="flag files with more than this %% of pixels at 1-")
    ap.add_argument("--no-vessel-check", action="store_true", help="skip the rembg check that a crop does not cut the plate (on by default when rembg is installed)")
    args = ap.parse_args()

    session = C.load_session(args.session)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.variants:
        ref = Path(args.variants)
        base = C.params_for(session, ref.name)
        alt = args.alt_contrast if args.alt_contrast is not None else base["contrast"]["strength"] + 2.0
        rgb8 = C.load_rgb8(ref)
        written = []
        for strength in (base["contrast"]["strength"], alt):
            for ev in (-0.3, 0.0, 0.3):
                p = json.loads(json.dumps(base))
                p["exposure"] = base["exposure"] * (2.0 ** ev)
                p["contrast"]["strength"] = strength
                name = f"{ref.stem}__ev{ev:+.1f}_c{strength:.1f}.jpg"
                C.save_jpeg(out_dir / name, grade_array(rgb8, p))
                written.append(name)
                C.log(f"  {name}")
        C.log(f"6 variants of {ref.name} -> {out_dir}")
        return 0

    if not args.inp:
        C.die("--in is required (or use --variants)")
    files = C.list_images(args.inp)
    if not files:
        C.die(f"no images in {args.inp}")
    jobs = []
    skipped = [src.name for src in files if C.is_excluded(session, src.name)]
    if skipped:
        C.log(f"  excluded by the session: {', '.join(skipped)}")
    files = [src for src in files if not C.is_excluded(session, src.name)]
    if not files:
        C.die("every input is on the session's exclude list")
    for src in files:
        p = C.params_for(session, src.name)
        p["_overridden"] = C.stem_of(src.name) in {C.stem_of(k) for k in session.get("overrides", {})}
        jobs.append((src, out_dir / f"{src.stem}.jpg", p))

    results = {}
    if args.workers > 1 and len(jobs) > 1:
        from multiprocessing import Pool
        with Pool(args.workers) as pool:
            for name, st in pool.imap_unordered(_worker, jobs):
                results[name] = st
                C.log(f"  {name}: Y50 {st['y_median']:.3f} clip {st['clip_high_pct']:.2f}%{' (override)' if st['overridden'] else ''}")
    else:
        for job in jobs:
            name, st = _worker(job)
            results[name] = st
            C.log(f"  {name}: Y50 {st['y_median']:.3f} clip {st['clip_high_pct']:.2f}%{' (override)' if st['overridden'] else ''}")

    if args.qa:
        flags = []
        if not args.no_vessel_check:
            flags += vessel_check(files, out_dir)
        for name in sorted(results):
            st = results[name]
            if st["clip_high_pct"] > args.clip_high_warn:
                flags.append({"file": Path(name).stem + ".jpg", "code": "clip_high", "value": st["clip_high_pct"],
                              "detail": f"{st['clip_high_pct']}% of pixels at 254+ (white plate or specular); try exposure_ev=-0.2 or a higher highlights value"})
            if st["clip_low_pct"] > args.clip_low_warn:
                flags.append({"file": Path(name).stem + ".jpg", "code": "clip_low", "value": st["clip_low_pct"],
                              "detail": f"{st['clip_low_pct']}% of pixels at 1-; try a shadows lift or exposure_ev=+0.2"})
        C.update_stats(Path(args.qa) / "grade_stats.json", {C.stem_of(k): v for k, v in results.items()})
        C.write_flags(args.qa, "grade", list(results), flags)
        C.log(f"stats -> {Path(args.qa) / 'grade_stats.json'}; {len(flags)} flag(s)")
    C.log(f"graded {len(results)} file(s) -> {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
