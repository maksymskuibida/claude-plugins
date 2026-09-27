#!/usr/bin/env python3
"""Programmatic checks for one eval run; prints JSON the grader merges.

    grade_run.py <eval-name> <run-dir>      # run-dir holds outputs/

Notes-based assertions are left for the grader; this script settles the
ones a script can settle (files, colour, seams, checksums).
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent / "skills" / "dish-media" / "scripts"
sys.path.insert(0, str(SCRIPTS))
import _common as C  # noqa: E402
import frame_grid as FG  # noqa: E402

TRUTH = json.loads((HERE / "results" / "data" / "evals" / "truth.json").read_text())
INPUTS = HERE / "results" / "data" / "evals"


def jpgs(root: Path):
    return [p for p in root.rglob("*.jpg") if not ({"raw", "downloads", "work", "mattes"} & set(p.parts))]


def mp4s(root: Path):
    return [p for p in root.rglob("*") if p.suffix.lower() in (".mp4", ".mov") and "raw" not in p.parts]


def probe(p: Path) -> dict:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,codec_name,pix_fmt,width,height,color_transfer,color_space:format=duration",
                        "-of", "json", str(p)], capture_output=True, text=True)
    d = json.loads(r.stdout or "{}")
    v = next((s for s in d.get("streams", []) if s.get("codec_type") == "video"), {})
    a = [s for s in d.get("streams", []) if s.get("codec_type") == "audio"]
    return {"video": v, "audio": len(a), "duration": float(d.get("format", {}).get("duration") or 0)}


def faststart(p: Path) -> bool:
    b = p.read_bytes()[:4096]
    return b.find(b"moov") != -1 and (b.find(b"mdat") == -1 or b.find(b"moov") < b.find(b"mdat"))


def seam(p: Path) -> tuple[float, int]:
    import cv2
    w, h, fps, n = FG.probe(str(p))
    first = last = None
    for i, fr in enumerate(FG.stream_frames(str(p), w, h)):
        g = cv2.resize(cv2.cvtColor(fr, cv2.COLOR_RGB2GRAY), (160, max(8, round(160 * h / w))), interpolation=cv2.INTER_AREA)
        if i == 0:
            first = g
        last = g
    return FG.ncc(last, first), n


def effective_gains(raw_path: Path, graded_path: Path) -> tuple[float, float, float] | None:
    """(R/G change, B/G change, match score) between a raw frame and its graded output.

    The graded file may be cropped and resized, so the output is located in
    the raw frame by greyscale template matching over a range of scales.
    Ratios are taken over matched pixels of medium brightness in linear light,
    so a tone curve (which scales all channels alike) cancels out."""
    import cv2
    raw = C.load_rgb8(raw_path)
    out = C.load_rgb8(graded_path)
    best = None
    for scale in np.arange(0.28, 0.62, 0.02):
        rs = cv2.resize(raw, (max(8, round(raw.shape[1] * scale)), max(8, round(raw.shape[0] * scale))), interpolation=cv2.INTER_AREA)
        if rs.shape[0] < out.shape[0] or rs.shape[1] < out.shape[1]:
            continue
        g1 = cv2.cvtColor(rs, cv2.COLOR_RGB2GRAY)
        g2 = cv2.cvtColor(out, cv2.COLOR_RGB2GRAY)
        res = cv2.matchTemplate(g1, g2, cv2.TM_CCOEFF_NORMED)
        _, val, _, loc = cv2.minMaxLoc(res)
        if best is None or val > best[0]:
            best = (val, scale, loc, rs)
    if best is None or best[0] < 0.6:
        return None
    val, scale, (x, y), rs = best
    a = C.srgb_to_linear(rs[y:y + out.shape[0], x:x + out.shape[1]].astype(np.float32) / 255.0).reshape(-1, 3)
    b = C.srgb_to_linear(out.astype(np.float32) / 255.0).reshape(-1, 3)
    ya = a @ C.LUMA_709
    chroma = np.abs(a[:, 0] / np.maximum(a[:, 1], 1e-4) - 1) + np.abs(a[:, 2] / np.maximum(a[:, 1], 1e-4) - 1)
    sel = (ya > 0.08) & (ya < 0.7) & (chroma < 0.6)     # near-neutral raw pixels: saturation scaling cannot bias them much
    if sel.sum() < 500:
        sel = (ya > 0.08) & (ya < 0.7)
    ma, mb = a[sel].mean(axis=0), b[sel].mean(axis=0)
    return float((mb[0] / mb[1]) / (ma[0] / ma[1])), float((mb[2] / mb[1]) / (ma[2] / ma[1])), float(val)


def plate_neutrality(rgb8: np.ndarray) -> float:
    lin = C.srgb_to_linear(rgb8.astype(np.float32) / 255.0)
    y = lin @ C.LUMA_709
    chroma = np.abs(lin[..., 0] / np.maximum(lin[..., 1], 1e-4) - 1) + np.abs(lin[..., 2] / np.maximum(lin[..., 1], 1e-4) - 1)
    cand = (y > np.percentile(y, 85)) & (y < 0.92) & (chroma < np.percentile(chroma, 40))
    m = C.linear_to_srgb(lin[cand].reshape(-1, 3).mean(axis=0)) * 255
    return float(max(abs(m[0] - m[1]), abs(m[2] - m[1])))


def find_graded(root: Path, stems: list[str]) -> dict:
    """Prefer out/photos, then deliver/photos, then any folder holding most stems."""
    best = {}
    for cand in [root / "project" / "out" / "photos", root / "project" / "deliver" / "photos"] + sorted({p.parent for p in jpgs(root)}):
        if not cand.is_dir():
            continue
        hit = {s: cand / f"{s}.jpg" for s in stems if (cand / f"{s}.jpg").is_file()}
        if len(hit) > len(best):
            best = hit
    return best


def checksums_ok(name: str) -> bool:
    files = sorted(p for p in (INPUTS / name).rglob("*") if p.is_file() and not p.name.startswith("."))
    now = sorted(subprocess.run(["md5", "-q", str(p)], capture_output=True, text=True).stdout.strip() for p in files)
    ref = sorted(l for l in (INPUTS / ".inputs.md5").read_text().split() if l)
    return all(h in ref for h in now)


def main() -> int:
    name, run = sys.argv[1], Path(sys.argv[2])
    out = run / "outputs"
    notes = ""
    for cand in list(out.rglob("notes.md"))[:1] + list(out.rglob("*.md")):
        try:
            notes += cand.read_text(errors="ignore") + "\n"
        except OSError:
            pass
    res = {}
    from PIL import Image

    if name == "session-with-card":
        stems = [f"IMG_{i:04d}" for i in range(2, 13)]
        graded = find_graded(out, stems)
        sess = next(iter(out.rglob("session.json")), None)
        excl = set()
        if sess:
            try:
                excl = {C.stem_of(x) for x in (json.loads(sess.read_text()).get("exclude") or [])}
            except json.JSONDecodeError:
                pass
        excluded = [s for s in stems if s not in graded and (s in excl or re.search(s + r".{0,160}(exclud|hand|chopstick|can|clutter|reshoot|not a dish|skip)", notes, re.I | re.S))]
        res["graded_all_11"] = (len(graded) + len(excluded) == 11, f"{len(graded)}/11 graded: {sorted(graded)}; excluded with a reason: {excluded}")
        bad = []
        for s, p in graded.items():
            with Image.open(p) as im:
                icc = im.info.get("icc_profile")
                ok_icc = True
                if icc:
                    from PIL import ImageCms
                    import io
                    desc = ImageCms.getProfileDescription(ImageCms.ImageCmsProfile(io.BytesIO(icc)))
                    ok_icc = "srgb" in desc.lower()
                orient = im.getexif().get(274, 1)
                if im.format != "JPEG" or not ok_icc or max(im.size) > 2560 or orient not in (None, 1):
                    bad.append(f"{p.name}: {im.format} icc_ok={ok_icc} {im.size} orient={orient}")
        res["delivery_format"] = (bool(graded) and not bad, "; ".join(bad) or f"{len(graded)} files JPEG/sRGB/<=2560/orientation ok")
        card_note = "card frame not delivered"
        card_ok = True
        cardp = find_graded(out, ["IMG_0001"]).get("IMG_0001")
        if cardp:
            a = C.load_rgb8(cardp)
            bx, by, bw, bh = TRUTH["session-with-card"]["card"]["card_box_raw"]
            # the raw frame is 3060x4080 portrait; scale to the output, whatever crop happened is unknown: search the patch
            sc = a.shape[1] / 3060
            x, y, w, h = int(bx * sc), int(by * sc), int(bw * sc), int(bh * sc)
            if y + h <= a.shape[0] and x + w <= a.shape[1]:
                m = a[y + 10:y + h - 10, x + 10:x + w - 10].reshape(-1, 3).mean(axis=0)
                card_ok = max(abs(m[0] - m[1]), abs(m[2] - m[1])) <= 3
                card_note = f"card patch in graded IMG_0001 reads {m.round(1)}"
            else:
                card_note = "card frame delivered but cropped; patch not found"
        want = TRUTH["session-with-card"]["card"]["tint_ratios"]          # the cast the card carries
        want_r, want_b = 1.0 / want["r_over_g"], 1.0 / want["b_over_g"]    # the correction that neutralises it
        eff, pairs = [], []
        for stem in ("IMG_0002", "IMG_0003", "IMG_0005"):
            if stem in graded:
                e = effective_gains(INPUTS / name / "raw" / "photos" / f"{stem}.jpg", graded[stem])
                if e:
                    eff.append(e)
                    pairs.append(f"{stem}: R/G x{e[0]:.3f} B/G x{e[1]:.3f} (match {e[2]:.2f})")
        wb_ok = bool(eff) and all(abs(e[0] - want_r) <= 0.06 and abs(e[1] - want_b) <= 0.08 for e in eff)
        res["white_balance"] = (card_ok and wb_ok, f"{card_note}; wanted R/G x{want_r:.3f} B/G x{want_b:.3f}; measured " + ("; ".join(pairs) or "no matchable pair"))
        loops = [p for p in mp4s(out) if any(k in p.stem for k in ("0013", "0014", "moon", "swift"))]
        # the fixture clips are not dishes; a run that says so and delivers no loop made the right call
        refused = not loops and bool(re.search(r"(0013|0014|clip|video|footage).{0,300}(not (a |your |the )?(dish|food)|moon|satellite|no food|exclud)", notes, re.I | re.S))
        info = {p.name: probe(p) for p in loops}
        fmt_ok = len(loops) >= 2 and all(i["video"].get("codec_name") == "h264" and i["video"].get("pix_fmt") == "yuv420p" and i["audio"] == 0
                                         and max(int(i["video"].get("width", 0)), int(i["video"].get("height", 0))) <= 1920 for i in info.values())
        refused_note = "no loop delivered; the notes name the clips as non-dish content (moon / satellite), which is the right call for a menu"
        res["loops_format"] = (refused or (fmt_ok and all(faststart(p) for p in loops)), refused_note if refused else json.dumps({k: (v["video"].get("codec_name"), v["video"].get("pix_fmt"), v["video"].get("width"), v["video"].get("height"), v["audio"], round(v["duration"], 2)) for k, v in info.items()}))
        seams = {p.name: seam(p) for p in loops}
        res["loops_seamless"] = (refused or (len(seams) >= 2 and all(s >= 0.95 for s, _ in seams.values())), refused_note if refused else json.dumps({k: (round(s, 4), n) for k, (s, n) in seams.items()}))
        rev = {}
        for p, (s, n) in seams.items():
            want = 360 if "0013" in p or "moon" in p else 300
            rev[p] = abs(n - want) <= 9 or bool(re.search(r"ping", notes, re.I))
        res["loops_one_revolution"] = (refused or (len(rev) >= 2 and all(rev.values())), refused_note if refused else json.dumps({k: seams[k][1] for k in rev}) + ("; notes mention ping-pong" if re.search(r"ping", notes, re.I) else ""))
        sheets = [p for p in jpgs(out) if p.parent.name in ("sheets", "qa") or "sheet" in p.name.lower() or "contact" in p.name.lower()]
        sheet_sizes = {}
        for p in sheets:
            with Image.open(p) as im:
                sheet_sizes[p.name] = im.size
        res["contact_sheets"] = (bool(sheet_sizes) and all(max(s) <= 1568 for s in sheet_sizes.values()) and bool(re.search(r"sheet", notes, re.I)), json.dumps(sheet_sizes))
        rep = list(out.rglob("report.md"))
        txt = rep[0].read_text() if rep else ""
        res["qa_report"] = (bool(rep) and bool(re.search(r"clip_high|outlier|cast|matte|`\w+`", txt)), rep[0].relative_to(out).as_posix() if rep else "no report.md")
        res["originals_untouched"] = (checksums_ok(name), "checksums compared with the pre-run list")

    elif name == "no-card-mixed":
        stems = [p.stem for p in sorted((INPUTS / name / "raw" / "photos").glob("*.jpg"))]
        graded = find_graded(out, stems)
        excluded = [s for s in stems if s not in graded and re.search(s + r".{0,120}(exclud|unusable|not a dish|interior|skip|reshoot|drop)", notes, re.I | re.S)]
        res["all_graded_or_excluded"] = (len(graded) + len(excluded) == len(stems), f"graded {sorted(graded)}, excluded with reason {excluded}")
        bg = np.array([246, 244, 239])
        cut = []
        for p in jpgs(out):
            if p.parent.name in ("photos", "variants", "sheets", "grids") and "cutout" not in p.parent.name:
                continue
            a = C.load_rgb8(p)
            if max(a.shape) > 1568 or a.shape[1] > 900:  # ignore sheets by size heuristics? sheets are 1568 wide; cutouts <= 1600 too
                pass
            corners = np.stack([a[:12, :12], a[:12, -12:], a[-12:, :12], a[-12:, -12:]]).reshape(-1, 3).mean(axis=0)
            if np.abs(corners - bg).max() <= 4:
                cut.append(p)
        deliver_dir = next((d for d in (out / "project" / "deliver" / "photos", out / "project" / "delivery") if d.is_dir() and list(d.glob("*.jpg"))), None)
        delivered = sorted(deliver_dir.glob("*.jpg")) if deliver_dir else []
        cut_names = {c.name for c in cut}
        not_cut = [d.name for d in delivered if d.name not in cut_names]
        explained = [n for n in not_cut if re.search(Path(n).stem + r".{0,400}(matte|cutout|cut out|mask|bowl|component|missing|drop|graded photo|instead)", notes, re.I | re.S)]
        ok_cut = bool(cut) and bool(delivered) and all(n in explained for n in not_cut)
        res["cutouts_on_cream"] = (ok_cut, f"{len(cut)} cutouts with #F6F4EF corners; delivered {[d.name for d in delivered]}; delivered without a cutout: {not_cut} (explained in the notes: {explained})")
        shadow = {}
        for p in cut:
            a = C.load_rgb8(p).astype(np.int32)
            y = a @ np.array([0.2126, 0.7152, 0.0722])
            bgy = float(bg @ np.array([0.2126, 0.7152, 0.0722]))
            band = ((y < bgy - 3) & (y > bgy - 60)).mean()
            shadow[p.name] = round(float(band), 4)
        res["soft_shadow"] = (bool(shadow) and all(v >= 0.005 for v in shadow.values()), json.dumps(shadow))
        res["no_card_explained"] = (bool(re.search(r"(no|without|missing|never).{0,40}(grey|gray) card|plate.{0,60}(neutral|white balance|reference)|neutral reference", notes, re.I)), "notes searched for the neutral-reference statement")
        res["interiors_identified"] = (all(re.search(k + r".{0,160}(interior|dining|room|no dish|not a dish|unusable|exclud|not .{0,20}food|scene)", notes, re.I | re.S) for k in ("khmer_01", "khmer_02")), "notes searched for khmer_01/02 as interiors")
        res["cross_session_reported"] = (bool(re.search(r"outlier|different (camera|light|session|source)|cannot .{0,30}(match|share)|one look", notes, re.I)), "notes searched")
        meds = {}
        deliver = next((d for d in (out / "project" / "deliver" / "photos", out / "project" / "delivery", out / "project" / "deliver") if d.is_dir() and list(d.glob("*.jpg"))), None)
        for p in (sorted(deliver.glob("*.jpg")) if deliver else [graded[s] for s in sorted(graded)]):
            a = C.load_rgb8(p)
            f = a.astype(np.float32) / 255.0
            y = f @ C.LUMA_709
            # a plain background (cutout) would dominate the median: measure the subject only
            corners = np.stack([a[:8, :8], a[:8, -8:], a[-8:, :8], a[-8:, -8:]]).reshape(-1, 3).mean(axis=0)
            plain = np.abs(a.astype(np.int32) - corners.astype(np.int32)).max(axis=2) <= 6
            sel = ~plain if plain.mean() > 0.3 else np.ones(y.shape, bool)
            meds[p.stem] = float(np.median(y[sel])) if sel.sum() > 1000 else float(np.median(y))
        spread = (max(meds.values()) - min(meds.values())) if meds else 9
        res["delivered_set_even"] = (bool(meds) and spread <= 0.20, f"Y50 per delivered photo {json.dumps({k: round(v, 2) for k, v in meds.items()})}; spread {spread:.2f} (limit 0.20)")
        sheets = [p for p in jpgs(out) if "sheet" in p.parent.name or "sheet" in p.name.lower() or "contact" in p.name.lower()]
        sizes = {}
        for p in sheets:
            with Image.open(p) as im:
                sizes[p.name] = im.size
        res["contact_sheets"] = (bool(sizes) and all(max(s) <= 1568 for s in sizes.values()), json.dumps(sizes))
        stats = list(out.rglob("cutout_stats.json"))
        res["matte_quality_assessed"] = (bool(stats) or bool(re.search(r"confidence|matte", notes, re.I)), "cutout_stats.json present" if stats else "notes searched for matte discussion")
        res["originals_untouched"] = (checksums_ok(name), "checksums compared with the pre-run list")

    elif name == "loops-and-hdr":
        loops = {k: next((p for p in mp4s(out) if p.stem.lower().startswith(k) or k in p.stem.lower()), None) for k in ("moon-hdr", "moon", "swift", "al92")}
        if loops["moon"] and loops["moon-hdr"] and loops["moon"] == loops["moon-hdr"]:
            loops["moon"] = next((p for p in mp4s(out) if "moon" in p.stem.lower() and "hdr" not in p.stem.lower()), None)
        info = {k: probe(p) for k, p in loops.items() if p}
        fmt_ok = len(info) == 4 and all(i["video"].get("codec_name") == "h264" and i["video"].get("pix_fmt") == "yuv420p" and i["audio"] == 0
                                        and max(int(i["video"].get("width", 0)), int(i["video"].get("height", 0))) <= 1920 for i in info.values())
        res["four_loops_format"] = (fmt_ok, json.dumps({k: (v["video"].get("codec_name"), v["video"].get("pix_fmt"), v["video"].get("width"), v["video"].get("height"), v["audio"], round(v["duration"], 2)) for k, v in info.items()}))
        seams = {k: seam(loops[k]) for k in ("moon", "swift") if loops.get(k)}
        res["moon_swift_seamless"] = (len(seams) == 2 and all(s >= 0.95 for s, _ in seams.values()), json.dumps({k: (round(s, 4), n) for k, (s, n) in seams.items()}))
        import cv2

        def mid_frame(p: Path):
            w, h, fps, n = FG.probe(str(p))
            for i, fr in enumerate(FG.stream_frames(str(p), w, h)):
                if i == n // 2:
                    return fr
            return fr
        hdr_ok, note = False, "missing loops"
        if loops.get("moon") and loops.get("moon-hdr"):
            ya = (mid_frame(loops["moon"]).astype(np.float32) @ np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)).mean()
            yb = (mid_frame(loops["moon-hdr"]).astype(np.float32) @ np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)).mean()
            trc = info["moon-hdr"]["video"].get("color_transfer", "")
            hdr_ok = abs(yb - ya) <= 0.10 * max(ya, 1) and trc in ("bt709", "", "unknown", "iec61966-2-1") and info["moon-hdr"]["video"].get("pix_fmt") == "yuv420p"
            note = f"mean Y moon {ya:.1f} vs moon-hdr {yb:.1f}; transfer={trc}"
        res["hdr_tonemapped"] = (hdr_ok, note)
        look_ok, note = False, "missing"
        if loops.get("moon"):
            src = mid_frame(INPUTS / name / "raw" / "video" / "moon.mov").astype(np.float32).reshape(-1, 3)
            dst = mid_frame(loops["moon"]).astype(np.float32).reshape(-1, 3)
            def ratios(v):
                lin = C.srgb_to_linear(v / 255.0)
                m = lin.mean(axis=0)
                return m[0] / max(m[1], 1e-4), m[2] / max(m[1], 1e-4)
            rs, bs = ratios(src)
            rd, bd = ratios(dst)
            look_ok = rd < rs - 0.02 and bd > bs + 0.02
            note = f"source R/G {rs:.3f} B/G {bs:.3f} -> loop R/G {rd:.3f} B/G {bd:.3f} (session gains 0.837 / 1.187)"
        res["session_look_applied"] = (look_ok, note)
        res["al92_ambiguity_handled"] = (bool(re.search(r"al92.{0,400}(ambigu|symmetr|half|ping|duration|grid|weak|uncertain)", notes, re.I | re.S)), "notes searched")
        grids = [p for p in jpgs(out) if "grid" in p.parent.name or "grid" in p.name.lower() or "sheet" in p.name.lower()]
        sizes = {}
        for p in grids:
            with Image.open(p) as im:
                sizes[p.name] = im.size
        res["frame_grids"] = (len(sizes) >= 4 and all(max(s) <= 1568 for s in sizes.values()), json.dumps(sizes))
        vs = list(out.rglob("video_stats.json"))
        n_seam = 0
        if vs:
            d = json.loads(vs[0].read_text())
            n_seam = sum(1 for v in d.values() if isinstance(v, dict) and v.get("seam_similarity") is not None)
        res["seam_scores_recorded"] = (n_seam >= 3 or len(re.findall(r"seam[^\n]{0,40}\d\.\d{2}", notes, re.I)) >= 3, f"video_stats seams={n_seam}; notes seam mentions={len(re.findall(r'seam', notes, re.I))}")
        res["originals_untouched"] = (checksums_ok(name), "checksums compared with the pre-run list")

    elif name == "pattaya-menu":
        stems = [Path(n).stem for n in TRUTH["pattaya-menu"]["photos"]]
        graded = find_graded(out, stems)
        sess = next(iter(out.rglob("session.json")), None)
        excl = set()
        if sess:
            try:
                excl = {C.stem_of(x) for x in (json.loads(sess.read_text()).get("exclude") or [])}
            except json.JSONDecodeError:
                pass
        excluded = [st for st in stems if st not in graded and (st in excl or re.search(st + r".{0,160}(exclud|reshoot|unusable|cut)", notes, re.I | re.S))]
        res["all_graded_or_excluded"] = (len(graded) + len(excluded) == len(stems), f"graded {len(graded)}/{len(stems)}; excluded with a reason: {excluded}")
        bad = []
        for st, pth in graded.items():
            with Image.open(pth) as im:
                icc = im.info.get("icc_profile"); ok_icc = True
                if icc:
                    from PIL import ImageCms
                    import io
                    ok_icc = "srgb" in ImageCms.getProfileDescription(ImageCms.ImageCmsProfile(io.BytesIO(icc))).lower()
                if im.format != "JPEG" or not ok_icc or max(im.size) > 2560 or im.getexif().get(274, 1) not in (None, 1):
                    bad.append(pth.name)
        res["delivery_format"] = (bool(graded) and not bad, "; ".join(bad) or f"{len(graded)} files JPEG/sRGB/<=2560/orientation ok")
        deliver = next((d for d in (out / "project" / "deliver" / "photos", out / "project" / "delivery") if d.is_dir() and list(d.glob("*.jpg"))), None)
        delivered = sorted(deliver.glob("*.jpg")) if deliver else [graded[st] for st in sorted(graded)]
        neut = {pth.stem: round(plate_neutrality(C.load_rgb8(pth)), 1) for pth in delivered}
        res["plates_neutral"] = (sum(1 for v in neut.values() if v <= 6) >= min(12, len(neut)) and bool(neut), json.dumps(neut))
        p95 = {pth.stem: C.image_stats(C.load_rgb8(pth))["y_p95"] for pth in delivered}
        spread = (max(p95.values()) - min(p95.values())) if p95 else 9
        res["batch_even"] = (bool(p95) and spread <= 0.15, f"plate p95 per photo {json.dumps({k: round(v, 2) for k, v in p95.items()})}; spread {spread:.2f} (limit 0.15)")
        # the vessel is whole: matte border contact on the delivered file vs on the source
        import cutout as CO
        model, _ = CO.load_model(CO.DEFAULT_MODEL)
        cuts = {}
        for pth in delivered:
            src = INPUTS / name / "raw" / "photos" / f"{pth.stem}.jpg"
            a_out = CO.confidence(CO.matte_for(C.load_rgb8(pth), model))[1]["border_touch"]
            a_src = CO.confidence(CO.matte_for(C.load_rgb8(src), model))[1]["border_touch"] if src.is_file() else 0.0
            cuts[pth.stem] = (round(a_out, 3), round(a_src, 3))
        bad_cut = [k for k, (o, s_) in cuts.items() if o > 0.01 and o > s_ + 0.005]
        res["vessel_whole"] = (bool(cuts) and not bad_cut, f"border contact out/src per photo {json.dumps(cuts)}; cut worse than source: {bad_cut}")
        plain = []
        for pth in delivered:
            a = C.load_rgb8(pth)
            corners = np.stack([a[:12, :12], a[:12, -12:], a[-12:, :12], a[-12:, -12:]]).reshape(4, -1, 3).mean(axis=1)
            if np.abs(corners - corners.mean(axis=0)).max() <= 4 and a.reshape(-1, 3).std(axis=0).mean() < 60:
                plain.append(pth.name)
        res["real_background_kept"] = (bool(delivered) and not plain, f"plain-background deliveries: {plain}")
        sheets = [q for q in jpgs(out) if "sheet" in q.parent.name or "sheet" in q.name.lower() or "survey" in q.name.lower()]
        sizes = {}
        for q in sheets:
            with Image.open(q) as im:
                sizes[q.name] = im.size
        res["contact_sheets"] = (bool(sizes) and all(max(v) <= 1568 for v in sizes.values()) and bool(re.search(r"sheet", notes, re.I)), json.dumps(sizes))
        rep = list(out.rglob("report.md"))
        txt = rep[0].read_text() if rep else ""
        res["qa_report"] = (bool(rep) and bool(re.search(r"`\w+`", txt)), rep[0].relative_to(out).as_posix() if rep else "no report.md")
        res["originals_untouched"] = (checksums_ok(name), "checksums compared with the pre-run list")

    elif name == "loops-real":
        T = TRUTH["loops-real"]
        dish_clips = [Path(c).stem for c in T["clips"] if c not in T["not_a_dish"]]
        loops = {}
        for st in dish_clips + ["cake_with_baker"]:
            loops[st] = next((q for q in mp4s(out) if q.stem == st), None)
        info = {k: probe(v) for k, v in loops.items() if v}
        fmt_ok = all(loops.get(st) for st in dish_clips) and all(i["video"].get("codec_name") == "h264" and i["video"].get("pix_fmt") == "yuv420p" and i["audio"] == 0
                                                                 and max(int(i["video"].get("width", 0)), int(i["video"].get("height", 0))) <= 1920 for i in info.values())
        baker_ok = (loops.get("cake_with_baker") is None) or bool(re.search(r"cake_with_baker.{0,300}(person|woman|baker|people|exclud|not a dish|flag)", notes, re.I | re.S))
        res["dish_loops_format_baker_excluded"] = (fmt_ok and baker_ok, json.dumps({k: (v["video"].get("width"), v["video"].get("height"), v["video"].get("pix_fmt"), v["audio"], round(v["duration"], 2)) for k, v in info.items()}) + f"; baker handled={baker_ok}")
        seams = {k: seam(v) for k, v in loops.items() if v and k in dish_clips}
        res["loops_seamless"] = (bool(seams) and all(s_ >= 0.95 for s_, _ in seams.values()), json.dumps({k: (round(s_, 4), n) for k, (s_, n) in seams.items()}))
        vs = next(iter(out.rglob("video_stats.json")), None)
        vstats = json.loads(vs.read_text()) if vs else {}
        partial_ok = []
        for st in [Path(c).stem for c in T["partial_rotation"]]:
            mode = (vstats.get(st) or {}).get("loop")
            ok_ = mode == "pingpong" or bool(re.search(st + r".{0,300}(ping|dissolve|crossfade)", notes, re.I | re.S))
            partial_ok.append((st, mode, ok_))
        res["partial_as_pingpong"] = (all(o for _, _, o in partial_ok), json.dumps(partial_ok))
        import cv2

        def mid_frame(pth: Path):
            w, h, fps, n = FG.probe(str(pth)); fr = None
            for i, fr in enumerate(FG.stream_frames(str(pth), w, h)):
                if i == n // 2:
                    return fr
            return fr
        hdr_ok, note = False, "missing loops"
        if loops.get("raspberries") and loops.get("raspberries-hdr"):
            ya = (mid_frame(loops["raspberries"]).astype(np.float32) @ np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)).mean()
            yb = (mid_frame(loops["raspberries-hdr"]).astype(np.float32) @ np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)).mean()
            trc = info["raspberries-hdr"]["video"].get("color_transfer", "")
            hdr_ok = abs(yb - ya) <= 0.10 * max(ya, 1) and trc in ("bt709", "", "unknown", "iec61966-2-1") and info["raspberries-hdr"]["video"].get("pix_fmt") == "yuv420p"
            note = f"mean Y raspberries {ya:.1f} vs hdr {yb:.1f}; transfer={trc}"
        res["hdr_tonemapped"] = (hdr_ok, note)
        cc = info.get("chocolate_cake", {}).get("video", {})
        res["portrait_kept"] = (bool(cc) and int(cc.get("height", 0)) > int(cc.get("width", 0)) and int(cc.get("height", 0)) <= 1920, f"chocolate_cake {cc.get('width')}x{cc.get('height')}")
        dirs = {k: v.get("direction") for k, v in vstats.items() if isinstance(v, dict) and v.get("direction") in ("cw", "ccw")}
        flags_json = next(iter(out.rglob("flags.json")), None)
        fl = json.loads(flags_json.read_text()).get("flags", []) if flags_json else []
        mismatch = any(f.get("code") == "direction_mismatch" for f in fl) or bool(re.search(r"cake_stand.{0,300}(other way|opposite|counter|ccw|mismatch|reverse)", notes, re.I | re.S))
        res["direction_recorded_and_odd_one_flagged"] = (len(dirs) >= 4 and mismatch, f"directions {json.dumps(dirs)}; odd one flagged={mismatch}")
        grids = [q for q in jpgs(out) if "grid" in q.parent.name or "grid" in q.name.lower()]
        sizes = {}
        for q in grids:
            with Image.open(q) as im:
                sizes[q.name] = im.size
        n_deliv = sum(1 for st in dish_clips if loops.get(st))
        res["frame_grids"] = (len(sizes) >= n_deliv and n_deliv > 0 and all(max(v) <= 1568 for v in sizes.values()), json.dumps(sizes))
        look_ok, note = False, "missing"
        if loops.get("tomato_juice"):
            srcf = mid_frame(INPUTS / name / "raw" / "video" / "tomato_juice.mov").astype(np.float32).reshape(-1, 3)
            dstf = mid_frame(loops["tomato_juice"]).astype(np.float32).reshape(-1, 3)
            def ratios(v):
                lin = C.srgb_to_linear(v / 255.0); m = lin.mean(axis=0)
                return m[0] / max(m[1], 1e-4), m[2] / max(m[1], 1e-4)
            rs, bs = ratios(srcf); rd, bd = ratios(dstf)
            look_ok = rd < rs - 0.02 and bd > bs + 0.02
            note = f"source R/G {rs:.3f} B/G {bs:.3f} -> loop R/G {rd:.3f} B/G {bd:.3f}"
        res["session_look_applied"] = (look_ok, note)
        res["originals_untouched"] = (checksums_ok(name), "checksums compared with the pre-run list")

    elif name == "amateur-menu":
        T = TRUTH["amateur-menu"]
        stems = [Path(n).stem for n in T["photos"]]
        graded = find_graded(out, stems)
        sess = next(iter(out.rglob("session.json")), None)
        excl = set()
        if sess:
            try:
                excl = {C.stem_of(x) for x in (json.loads(sess.read_text()).get("exclude") or [])}
            except json.JSONDecodeError:
                pass
        excluded = [st for st in stems if st not in graded and (st in excl or re.search(st + r".{0,200}(exclud|reshoot|unusable|not a dish|leave out|left out|packag|exterior|street)", notes, re.I | re.S))]
        res["all_graded_or_excluded"] = (len(graded) + len(excluded) == len(stems), f"graded {len(graded)}/{len(stems)}; excluded with a reason: {excluded}")
        bad = []
        for st, pth in graded.items():
            with Image.open(pth) as im:
                icc = im.info.get("icc_profile"); ok_icc = True
                if icc:
                    from PIL import ImageCms
                    import io
                    ok_icc = "srgb" in ImageCms.getProfileDescription(ImageCms.ImageCmsProfile(io.BytesIO(icc))).lower()
                if im.format != "JPEG" or not ok_icc or max(im.size) > 2560 or im.getexif().get(274, 1) not in (None, 1):
                    bad.append(pth.name)
        res["delivery_format"] = (bool(graded) and not bad, "; ".join(bad) or f"{len(graded)} files JPEG/sRGB/<=2560/orientation ok")
        deliver = next((d for d in (out / "project" / "deliver" / "photos", out / "project" / "delivery") if d.is_dir() and list(d.glob("*.jpg"))), None)
        delivered = sorted(deliver.glob("*.jpg")) if deliver else [graded[st] for st in sorted(graded)]

        # the patch each file was measured on, found again in the output through that file's own geometry
        import grade as G
        import cv2
        session_d = json.loads(sess.read_text()) if sess else None
        measures = {}
        for mj in out.rglob("*.json"):
            if mj.parent.name != "measure":
                continue
            try:
                d = json.loads(mj.read_text())
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            if d.get("schema") == "dish-media.measure/1" and d.get("card_region") and d.get("image"):
                measures[Path(d.get("source") or mj.stem).stem] = d
        patch = {}
        for pth in delivered:
            d = measures.get(pth.stem)
            if not d or not session_d:
                patch[pth.stem] = None
                continue
            reg, im_ = d["card_region"], d["image"]
            mask = np.zeros((int(im_["height"]), int(im_["width"]), 3), dtype=np.uint8)
            mask[int(reg["y"]):int(reg["y"]) + int(reg["h"]), int(reg["x"]):int(reg["x"]) + int(reg["w"])] = 255
            m = G.apply_geometry(mask, C.params_for(session_d, pth.name))
            outimg = C.load_rgb8(pth)
            sel = cv2.resize(m, (outimg.shape[1], outimg.shape[0]), interpolation=cv2.INTER_AREA)[..., 0] > 200
            if sel.sum() < 16:
                patch[pth.stem] = None   # the patch was cropped out
                continue
            lin = C.srgb_to_linear(outimg[sel].astype(np.float32) / 255.0).reshape(-1, 3).mean(axis=0)
            s = C.linear_to_srgb(lin) * 255.0
            patch[pth.stem] = (round(float(s[0] - s[2]), 1), round(float(max(abs(s[0] - s[1]), abs(s[2] - s[1]))), 1), round(float(C.linear_to_srgb(lin @ C.LUMA_709)), 3))
        have = {k: v for k, v in patch.items() if v}
        warm_ok = sum(1 for rb_, dev, _ in have.values() if 0.0 <= rb_ <= 12.0 and dev <= 12.0)
        res["patch_neutral_warm"] = (bool(have) and warm_ok >= 0.8 * len(have),
                                     f"each file's own white patch in the output as (R-B, max channel dev, Y): {json.dumps(patch)}; {warm_ok}/{len(have)} neutral with warmth, {len(patch) - len(have)} without a usable patch")
        bright_ok = sum(1 for _, _, yv in have.values() if 0.66 <= yv <= 0.86)
        res["patch_bright"] = (bool(have) and bright_ok >= 0.8 * len(have), f"{bright_ok}/{len(have)} patches land at Y 0.66-0.86 (values above)")
        import cutout as CO
        model, _ = CO.load_model(CO.DEFAULT_MODEL)
        mattes = {}

        def matte(pth):
            if pth not in mattes:
                mattes[pth] = CO.matte_for(C.load_rgb8(pth), model)
            return mattes[pth]

        p95 = {pth.stem: C.image_stats(C.load_rgb8(pth))["y_p95"] for pth in delivered}
        spread = (max(p95.values()) - min(p95.values())) if p95 else 9
        res["batch_even"] = (bool(p95) and spread <= 0.20, f"plate p95 per photo spread {spread:.2f} (limit 0.20)")
        cuts = {}
        for pth in delivered:
            src = INPUTS / name / "raw" / "photos" / f"{pth.stem}.jpg"
            a_out = CO.confidence(matte(pth))[1]["border_touch"]
            a_src = CO.confidence(matte(src))[1]["border_touch"] if src.is_file() else 0.0
            cuts[pth.stem] = (round(a_out, 3), round(a_src, 3))
        bad_cut = [k for k, (o, s_) in cuts.items() if o > 0.01 and o > s_ + 0.005]
        res["vessel_whole"] = (bool(cuts) and not bad_cut, f"cut worse than source: {bad_cut}; per photo out/src {json.dumps(cuts)}")
        nd = [Path(n).stem for n in T["not_a_dish"]]
        nd_ok = all(st in excluded or st not in {d.stem for d in delivered} or re.search(st + r".{0,200}(not a dish|exterior|street|packag|exclud|leave out)", notes, re.I | re.S) for st in nd)
        res["non_dish_excluded"] = (nd_ok, f"{nd} delivered? {[st for st in nd if st in {d.stem for d in delivered}]}")
        sheets = [q for q in jpgs(out) if "sheet" in q.parent.name or "sheet" in q.name.lower() or "survey" in q.name.lower()]
        sizes = {}
        for q in sheets:
            with Image.open(q) as im:
                sizes[q.name] = im.size
        res["contact_sheets"] = (bool(sizes) and all(max(v) <= 1568 for v in sizes.values()) and bool(re.search(r"sheet", notes, re.I)), json.dumps(sizes))
        rep = list(out.rglob("report.md"))
        txt = rep[0].read_text() if rep else ""
        res["qa_report"] = (bool(rep) and bool(re.search(r"`\w+`", txt)), rep[0].relative_to(out).as_posix() if rep else "no report.md")
        res["originals_untouched"] = (checksums_ok(name), "checksums compared with the pre-run list")

    elif name == "amateur-clips":
        T = TRUTH["amateur-clips"]
        dish = [Path(c).stem for c in T["dish_clips"]]
        nond = [Path(c).stem for c in T["not_a_dish"]]
        loops = {st: next((q for q in mp4s(out) if q.stem == st), None) for st in dish + nond}
        vs = next(iter(out.rglob("video_stats.json")), None)
        vstats = json.loads(vs.read_text()) if vs else {}
        info = {k: probe(v) for k, v in loops.items() if v}
        dish_ok = all((loops.get(st) and info[st]["video"].get("codec_name") == "h264" and info[st]["video"].get("pix_fmt") == "yuv420p" and info[st]["audio"] == 0
                       and max(int(info[st]["video"].get("width", 0)), int(info[st]["video"].get("height", 0))) <= 1920)
                      or re.search(st + r".{0,300}(left out|exclud|cannot|can't|not usable|unusable)", notes, re.I | re.S) for st in dish)
        res["dish_clips_delivered_or_declined"] = (dish_ok, json.dumps({k: (v["video"].get("width"), v["video"].get("height"), v["video"].get("pix_fmt"), round(v["duration"], 2)) for k, v in info.items()}))
        deliver_dir = out / "project" / "deliver" / "loops"
        delivered = [q.stem for q in deliver_dir.glob("*.mp4")] if deliver_dir.is_dir() else [k for k, v in loops.items() if v and k in dish]
        modes = {st: (vstats.get(st) or {}).get("loop") for st in delivered}
        res["no_fake_revolution"] = (bool(delivered) and all(m in ("pingpong", "none") for m in modes.values()), json.dumps(modes))
        nd_ok = all(loops.get(st) is None or re.search(st + r".{0,300}(exclud|not a dish|people|person|guest|chef|cook|stall|no plated|left out)", notes, re.I | re.S) for st in nond)
        res["non_dish_excluded"] = (nd_ok, f"non-dish clips rendered: {[st for st in nond if loops.get(st)]}")
        measures = list(out.rglob("*.json"))
        sess = next(iter(out.rglob("session.json")), None)
        ov = {}
        if sess:
            try:
                ov = json.loads(sess.read_text()).get("overrides") or {}
            except json.JSONDecodeError:
                pass
        per_clip = {st: (st in ov and "wb_gains" in ov[st]) or any(m.stem.startswith(st) and "measure" in m.parent.name for m in measures) for st in delivered}
        res["white_balance_measured_per_clip"] = (bool(per_clip) and all(per_clip.values()), json.dumps(per_clip))
        import cv2

        def mid_frame(pth: Path):
            w, h, fps, n = FG.probe(str(pth)); fr = None
            for i, fr in enumerate(FG.stream_frames(str(pth), w, h)):
                if i == n // 2:
                    return fr
            return fr

        def plate_rb(rgb8):
            lin = C.srgb_to_linear(rgb8.astype(np.float32) / 255.0)
            y = lin @ C.LUMA_709
            chroma = np.abs(lin[..., 0] / np.maximum(lin[..., 1], 1e-4) - 1) + np.abs(lin[..., 2] / np.maximum(lin[..., 1], 1e-4) - 1)
            cand = (y > np.percentile(y, 85)) & (y < 0.92) & (chroma < np.percentile(chroma, 40))
            m = C.linear_to_srgb(lin[cand].reshape(-1, 3).mean(axis=0)) * 255
            return float(m[0] - m[2])
        rb = {st: round(plate_rb(mid_frame(loops[st])), 1) for st in delivered if loops.get(st)}
        res["clips_warm_not_cold"] = (bool(rb) and all(1.0 <= v <= 14.0 for v in rb.values()), f"R-B of bright whites in a middle frame {json.dumps(rb)}")
        grids = [q for q in jpgs(out) if "grid" in q.parent.name or "grid" in q.name.lower()]
        sizes = {}
        for q in grids:
            with Image.open(q) as im:
                sizes[q.name] = im.size
        res["frame_grids"] = (len(sizes) >= len(delivered) and bool(delivered) and all(max(v) <= 1568 for v in sizes.values()), json.dumps(sizes))
        res["originals_untouched"] = (checksums_ok(name), "checksums compared with the pre-run list")

    print(json.dumps({k: {"passed": bool(v[0]), "evidence": v[1]} for k, v in res.items()}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
