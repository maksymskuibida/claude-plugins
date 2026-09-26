#!/usr/bin/env python3
"""Assertions over one full run of the pipeline on the synthetic shoot.

    check_pipeline.py PROJECT_DIR [--second-run PROJECT_DIR_2] [--skip-cutout]

Run by run_tests.sh after every script has run. Every check names what it
measured, so a failure says which promise the pipeline broke.
"""
from __future__ import annotations

import argparse
import csv
import filecmp
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _common as C  # noqa: E402

FAILS: list[str] = []
PASSES = 0


def check(cond: bool, what: str) -> None:
    global PASSES
    if cond:
        PASSES += 1
        print(f"  ok   {what}")
    else:
        FAILS.append(what)
        print(f"  FAIL {what}")


def card_in_output(path: Path, truth: dict) -> np.ndarray:
    """Mean RGB of the card patch in a graded (uncropped, 1600 px) output."""
    a = C.load_rgb8(path)
    scale = a.shape[1] / truth["width"]
    x, y, w, h = [int(v * scale) for v in truth["card_box"]]
    inset = max(4, int(min(w, h) * 0.15))
    return a[y + inset:y + h - inset, x + inset:x + w - inset].reshape(-1, 3).astype(np.float64).mean(axis=0)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("project")
    ap.add_argument("--second-run", help="a second project dir produced from the same inputs and session")
    ap.add_argument("--skip-cutout", action="store_true")
    args = ap.parse_args()
    P = Path(args.project)
    truth = json.loads((P / "fixtures.json").read_text())
    session = C.load_session(P / "work" / "session.json")
    target = session["provenance"]["targets"]["card_luminance_srgb"] * 255.0

    print("ingest")
    rows = list(csv.DictReader((P / "work" / "manifest.csv").open(newline="")))
    photos = [r for r in rows if r["kind"] == "photo"]
    check(len(photos) == len(truth["dishes"]), f"manifest lists all {len(truth['dishes'])} photos")
    check(all(r["work_file"] for r in photos), "every photo was ingested")
    check(any("heic" in r["source_profile"] for r in photos) or not any(n.lower().endswith(".heic") for n in truth["dishes"]),
          "the HEIC original went through the sips path")
    check(any(r["source_profile"].startswith("icc->") for r in photos), "the Display-P3-tagged JPEG was converted to sRGB")
    from PIL import Image
    for r in photos:
        with Image.open(P / "work" / "photos" / r["work_file"]) as im:
            check(max(im.size) == 2400 and im.info.get("icc_profile") is not None and "exif" not in im.info,
                  f"{r['work_file']}: 2400 px long edge, sRGB profile embedded, no EXIF")
    vids = [r for r in rows if r["kind"] == "video"]
    check(len(vids) == 2, "manifest lists both clips")
    flags = C.load_flags(P / "qa")
    check(any(f["code"] == "hdr_source" and f["file"] == "hdr-001.mov" for f in flags), "the HLG clip was flagged hdr_source at ingest")

    print("measure / calibrate")
    m = json.loads((P / "work" / "measure" / "card.json").read_text())
    r_g, b_g = m["ratios"]["r_over_g"], m["ratios"]["b_over_g"]
    check(abs(r_g - truth["cast"][0]) < 0.02 and abs(b_g - truth["cast"][2]) < 0.02,
          f"card ratios recover the synthetic cast (R/G {r_g:.3f} vs {truth['cast'][0]}, B/G {b_g:.3f} vs {truth['cast'][2]})")
    auto = json.loads((P / "work" / "measure" / "card-auto.json").read_text())
    ab = auto["card_region"]
    scale = m["image"]["width"] / truth["width"]
    tx, ty, tw, th = [v * scale for v in truth["card_box"]]
    inside = ab["x"] >= tx and ab["y"] >= ty and ab["x"] + ab["w"] <= tx + tw and ab["y"] + ab["h"] <= ty + th
    check(inside, f"measure.py --auto put its box inside the card ({ab})")
    pred = session["provenance"]["predicted_card_srgb255"]
    check(max(abs(pred[0] - pred[1]), abs(pred[2] - pred[1])) <= 1.0 and abs(pred[1] - target) <= 0.5,
          f"calibrate predicts a neutral card on target ({pred} vs {target:.1f})")
    check(abs(session["exposure"] - 1.0 / truth["under"]) < 0.35, f"derived exposure x{session['exposure']:.3f} is near the +1 EV that was taken away")

    print("grade")
    clean = [n for n, d in truth["dishes"].items() if d["ev"] == 0 and d["tilt_deg"] == 0 and not d.get("portrait") and not n.startswith("dish-005")]
    for name in clean:
        out = P / "out" / "photos" / (Path(name).stem + ".jpg")
        mean = card_in_output(out, truth)
        neutral = max(abs(mean[0] - mean[1]), abs(mean[2] - mean[1]))
        dev = 100.0 * (mean.mean() / target - 1.0)
        check(neutral <= 2.0, f"{out.name}: card neutral within 2/255 (max channel diff {neutral:.2f})")
        check(abs(dev) <= 3.0, f"{out.name}: card luminance within 3% of target ({mean.mean():.1f} vs {target:.1f}, {dev:+.2f}%)")
    with Image.open(P / "out" / "photos" / "dish-001.jpg") as im:
        check(max(im.size) == session["output_long_edge"] and im.info.get("icc_profile") is not None and "exif" not in im.info,
              "delivery JPEG: output_long_edge, sRGB profile, no EXIF")
    stats = json.loads((P / "qa" / "grade_stats.json").read_text())
    check(stats["dish-004"]["y_median"] > stats["dish-001"]["y_median"] + 0.05, "the +0.5 EV outlier dish is measurably brighter")
    check(any(f["code"] == "clip_high" and f["file"] == "dish-004.jpg" for f in flags), "the outlier dish was flagged clip_high by grade.py")
    ov = P / "out" / "photos-override" / "dish-004.jpg"
    if ov.is_file():
        mean = card_in_output(ov, truth)
        check(abs(mean.mean() - target) / target <= 0.04, f"exposure_ev=-0.5 override brings dish-004's card back to target ({mean.mean():.1f})")
    var = sorted((P / "qa" / "variants").glob("dish-001__*.jpg"))
    check(len(var) == 6, f"grade.py --variants wrote 6 renders ({len(var)})")
    with Image.open(P / "out" / "photos" / "dish-006.jpg") as im:
        check(im.size[1] > im.size[0] and abs(im.size[1] / im.size[0] - 4 / 3) < 0.01, f"portrait dish keeps a 3:4 upright crop, not a 4:3 slice ({im.size})")
    fm = P / "out" / "photos-filemeasure"
    sess_fm = C.load_session(P / "work" / "session-filemeasure.json")
    check("dish-004" in sess_fm["overrides"] and "wb_gains" in sess_fm["overrides"]["dish-004"], "--file-measure stored own gains and exposure as an override")
    if (fm / "dish-004.jpg").is_file():
        # dish-004 is +0.5 EV in the raw; its own card measurement must land it on target like the others
        mean = card_in_output(fm / "dish-004.jpg", truth)
        check(abs(mean.mean() - target) / target <= 0.03 and max(abs(mean[0] - mean[1]), abs(mean[2] - mean[1])) <= 2.0,
              f"--file-measure brings the outlier's card to target and neutral ({mean.round(1)})")
    check(not (fm / "dish-002.jpg").is_file() and (fm / "dish-001.jpg").is_file(), "an excluded file is skipped by grade.py, the others are not")
    check(sess_fm["provenance"].get("predicted_card_srgb255") is not None, "--from keeps the predicted card in provenance")

    print("contact sheets")
    for sheet in sorted((P / "qa" / "sheets").glob("*.jpg")) + [P / "qa" / "variants.jpg"]:
        with Image.open(sheet) as im:
            check(max(im.size) <= 1568, f"{sheet.name}: {im.size[0]}x{im.size[1]} fits in 1568 px")

    if not args.skip_cutout:
        print("cutout")
        cst = json.loads((P / "qa" / "cutout_stats.json").read_text())
        check(len(cst) == len(photos), "every photo has a cutout score")
        for stem, st in cst.items():
            check((P / "out" / "cutouts" / f"{stem}.jpg").is_file(), f"{stem}: cutout written")
            check(0.15 <= st["coverage"] <= 0.85, f"{stem}: matte covers a plausible area ({st['coverage']})")
        a = C.load_rgb8(P / "out" / "cutouts" / "dish-001.jpg")
        corner = a[:20, -20:].reshape(-1, 3).mean(axis=0)
        bg = np.array(C.parse_colour(session["background"]))
        check(np.abs(corner - bg).max() <= 3, f"cutout corner is the background colour ({corner.round(1)} vs {bg})")
        low = {stem for stem, st in cst.items() if st["confidence"] < 0.8}
        flagged = {C.stem_of(f["file"]) for f in flags if f["code"] == "matte_low_confidence"}
        check(low == flagged, f"every matte under 0.8 is flagged and nothing else is ({sorted(low)} vs {sorted(flagged)})")
        check(any(st.get("ghost", 0) > 0.01 for st in cst.values()), "the ghost check sees the faint grey card the model half-keeps in the fixtures")

    print("video")
    per = json.loads((P / "qa" / "period.json").read_text())
    check(per["period_s"] is not None and abs(per["period_s"] - truth["video"]["period_s"]) <= 0.1,
          f"loop_period.py found {per['period_s']} s (truth {truth['video']['period_s']} s, score {per['score']})")
    vst = json.loads((P / "qa" / "video_stats.json").read_text())
    check(vst.get("turn-001", {}).get("direction") == "ccw", f"the synthetic disc is reported turning ccw ({vst.get('turn-001', {}).get('direction')})")
    check(vst.get("turn-001-rev", {}).get("direction") == "cw", f"--reverse flips the reported direction to cw ({vst.get('turn-001-rev', {}).get('direction')})")
    check(any(f["code"] == "direction_mismatch" and f["file"] == "turn-001-rev.mp4" for f in flags), "qa_report flags the one loop that turns the other way")
    e = vst.get("turn-001-rev") or {}
    check(e.get("seam_similarity") is not None and e["seam_similarity"] >= 0.95, f"a reversed revolution still loops ({e.get('seam_similarity')})")
    for stem, loop in (("turn-001", "revolution"), ("turn-001-pp", "pingpong")):
        e = vst.get(stem) or {}
        check(e.get("loop") == loop, f"{stem}: exported as {loop}")
        check(e.get("seam_similarity") is not None and e["seam_similarity"] >= 0.95,
              f"{stem}: first/last frame similarity {e.get('seam_similarity')} >= 0.95")
        check(bool(e.get("seam_ok")), f"{stem}: seam not worse than a normal frame step ({e.get('typical_step_similarity')})")
        with Image.open(P / "qa" / "grids" / f"{stem}.jpg") as im:
            check(max(im.size) <= 1568, f"{stem}: frame grid {im.size[0]}x{im.size[1]} fits in 1568 px")
    rev, pp = vst.get("turn-001") or {}, vst.get("turn-001-pp") or {}
    check(rev.get("frames") == round(truth["video"]["period_s"] * truth["video"]["fps"]), "revolution loop is exactly one period of frames")
    check(rev.get("loop_frames") == rev.get("frames"), "decoder sees the same frame count the exporter wrote (revolution)")
    check(pp.get("frames") and pp.get("loop_frames") == 2 * pp["frames"] - 2, "ping-pong holds 2N-2 frames (no doubled frame at either end)")
    import subprocess
    pr = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_frames", "-show_entries",
                         "stream=pix_fmt,color_transfer,color_space,r_frame_rate,nb_read_frames,width,height", "-of", "json",
                         str(P / "out" / "loops" / "turn-001.mp4")], capture_output=True, text=True)
    s = json.loads(pr.stdout)["streams"][0]
    check(s["pix_fmt"] == "yuv420p" and s.get("color_transfer") == "bt709" and s.get("color_space") == "bt709",
          f"loop is yuv420p bt709 ({s['pix_fmt']}, {s.get('color_transfer')})")
    check(s.get("r_frame_rate") == f"{truth['video']['fps']}/1" and int(s.get("nb_read_frames", 0)) == round(truth["video"]["period_s"] * truth["video"]["fps"]),
          f"loop keeps the source frame rate and holds exactly one period ({s.get('r_frame_rate')}, {s.get('nb_read_frames')} frames)")
    check(max(int(s["width"]), int(s["height"])) <= 1920, f"loop long edge is at most 1920 ({s['width']}x{s['height']})")
    batch_flags = json.loads((P / "qa" / "flags-after-batch.json").read_text())["flags"]
    check(any(f["code"] == "video_failed" and f["file"] == "hdr-001.mov" for f in batch_flags), "grading the raw HLG clip fails per clip and is flagged, not fatal")
    check(not any(f["code"] == "video_failed" for f in flags), "the failure flag is cleared once the tone-mapped clip goes through")
    check((P / "out" / "loops" / "hdr-001.mp4").is_file(), "the tone-mapped HLG clip went through video_grade.py")
    pr = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=color_transfer", "-of",
                         "default=nw=1:nk=1", str(P / "work" / "sdr" / "hdr-001.mov")], capture_output=True, text=True)
    check(pr.stdout.strip() == "bt709", f"tonemap_hdr.sh output is tagged bt709 ({pr.stdout.strip()})")

    print("qa report / deliver")
    report = (P / "qa" / "report.md").read_text()
    check("exposure_outlier" in report and "dish-004.jpg" in report, "report names the exposure outlier")
    check("clip_high" in report and "loop_seam" not in report, "report carries clip_high and no loop_seam")
    check((P / "deliver" / "manifest.csv").is_file() and (P / "deliver" / "photos" / "dish-001.jpg").is_file()
          and (P / "deliver" / "loops" / "turn-001.mp4").is_file(), "deliver.py assembled photos, loops and a manifest")

    if args.second_run:
        print("determinism (second run from the same inputs and session)")
        Q = Path(args.second_run)
        same, differ = 0, []
        for rel in ("work/photos", "out/photos", "out/cutouts", "out/loops", "qa/sheets", "qa/variants", "qa/grids", "work/mattes"):
            d1, d2 = P / rel, Q / rel
            if not d1.is_dir():
                continue
            for f in sorted(d1.iterdir()):
                g = d2 / f.name
                if not g.is_file():
                    differ.append(f"{rel}/{f.name} (missing in second run)")
                elif filecmp.cmp(f, g, shallow=False):
                    same += 1
                else:
                    differ.append(f"{rel}/{f.name}")
        check(not differ and same > 0, f"{same} output files byte-identical across runs" + (f"; differ: {', '.join(differ)}" if differ else ""))
        for rel in ("work/session.cube", "qa/flags.json", "qa/grade_stats.json", "work/manifest.csv", "qa/report.md"):
            check(filecmp.cmp(P / rel, Q / rel, shallow=False), f"{rel} identical across runs")

    print()
    print(f"{PASSES} passed, {len(FAILS)} failed")
    for f in FAILS:
        print(f"  FAIL {f}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
