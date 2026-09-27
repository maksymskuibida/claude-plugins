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

    print(json.dumps({k: {"passed": bool(v[0]), "evidence": v[1]} for k, v in res.items()}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
