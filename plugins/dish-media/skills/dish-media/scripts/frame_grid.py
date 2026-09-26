#!/usr/bin/env python3
"""3 x 3 grid of frames from an exported loop, plus a seam score.

    frame_grid.py out/loops/dish-0412.mp4 --out qa/grids/dish-0412.jpg [--qa qa]

First frame, seven evenly spaced frames and the last frame, labelled with
their time, on one image of at most 1568 px so Claude can judge the look
and the loop seam in one Read. Prints JSON with `seam_similarity` (last
frame vs first frame) and `typical_step_similarity` (median of consecutive
frames). The seam is flagged when it is below 0.95 or clearly worse than
an ordinary frame step.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common as C  # noqa: E402
from contact_sheet import font  # noqa: E402

SEAM_MIN = 0.95
SEAM_STEP_MARGIN = 0.03


def ncc(a: np.ndarray, b: np.ndarray) -> float:
    a = a.astype(np.float32).ravel() - a.mean()
    b = b.astype(np.float32).ravel() - b.mean()
    d = float(np.linalg.norm(a) * np.linalg.norm(b))
    return float(a @ b / d) if d > 0 else 1.0


def probe(path: str) -> tuple[int, int, float, int]:
    """width, height, fps (r_frame_rate) and frame count via ffprobe."""
    import subprocess
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_frames", "-show_entries",
                        "stream=width,height,r_frame_rate,nb_read_frames", "-of", "json", path], capture_output=True, text=True)
    if r.returncode != 0:
        C.die(f"ffprobe failed on {path}: {r.stderr.strip()}")
    st = (json.loads(r.stdout).get("streams") or [{}])[0]
    num, _, den = (st.get("r_frame_rate") or "30/1").partition("/")
    return int(st["width"]), int(st["height"]), float(num) / float(den or 1), int(st.get("nb_read_frames") or 0)


def stream_frames(path: str, w: int, h: int):
    """Yield every frame as an RGB uint8 array, decoded by ffmpeg itself.

    OpenCV's reader can stop one frame short on a file whose container
    duration excludes the last frame, and the last frame is the one the
    seam check needs."""
    import subprocess
    proc = subprocess.Popen(["ffmpeg", "-v", "error", "-i", path, "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                            stdout=subprocess.PIPE)
    size = w * h * 3
    try:
        while True:
            buf = proc.stdout.read(size)
            if len(buf) < size:
                break
            yield np.frombuffer(buf, dtype=np.uint8).reshape(h, w, 3)
    finally:
        proc.stdout.close()
        proc.wait()


def main() -> int:
    import cv2
    from PIL import Image, ImageDraw
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("clip")
    ap.add_argument("--out", required=True, help="grid JPEG")
    ap.add_argument("--qa", help="qa folder for flags.json and video_stats.json")
    ap.add_argument("--max-edge", type=int, default=1568)
    args = ap.parse_args()

    w0, h0, fps, n = probe(args.clip)
    if n < 2:
        C.die(f"{args.clip} has {n} frame(s)")
    idx = sorted(set([0] + [round(k * (n - 1) / 8) for k in range(1, 8)] + [n - 1]))
    while len(idx) < 9:
        idx.append(n - 1)
    keep = set(idx)
    frames, small = {}, []
    for i, fr in enumerate(stream_frames(args.clip, w0, h0)):
        if i in keep:
            frames[i] = fr.copy()
        g = cv2.cvtColor(fr, cv2.COLOR_RGB2GRAY)
        small.append(cv2.resize(g, (160, max(8, round(160 * h0 / w0))), interpolation=cv2.INTER_AREA))
    if len(small) != n:
        C.die(f"decoded {len(small)} frames but ffprobe counted {n}")
    steps = [ncc(small[i], small[i + 1]) for i in range(n - 1)]
    typical = float(np.median(steps))
    seam = ncc(small[-1], small[0])
    ok_seam = seam >= SEAM_MIN and seam >= typical - SEAM_STEP_MARGIN
    idx = sorted(set([0] + [round(k * (n - 1) / 8) for k in range(1, 8)] + [n - 1]))
    while len(idx) < 9:
        idx.append(n - 1)

    gutter, label_h, header_h, cols = 8, 22, 24, 3
    cell_w = (args.max_edge - gutter * (cols + 1)) // cols
    thumb_h = round(cell_w * h0 / w0)
    W = args.max_edge
    H = header_h + 3 * (thumb_h + label_h + gutter) + gutter
    sheet = Image.new("RGB", (W, H), (128, 128, 128))
    d = ImageDraw.Draw(sheet)
    f = font(13)
    d.rectangle([0, 0, W, header_h], fill=(38, 38, 38))
    d.text((gutter, 5), f"{Path(args.clip).name}  {n} frames @ {fps:.2f} fps = {n / fps:.2f}s   seam {seam:.3f} (typical step {typical:.3f}) {'OK' if ok_seam else 'CHECK'}",
           fill=(235, 235, 235), font=font(14))
    for k, i in enumerate(idx[:9]):
        r, c = divmod(k, cols)
        x = gutter + c * (cell_w + gutter)
        y = header_h + gutter + r * (thumb_h + label_h + gutter)
        im = Image.fromarray(frames[i]).resize((cell_w, thumb_h), Image.Resampling.LANCZOS)
        sheet.paste(im, (x, y))
        d.rectangle([x, y + thumb_h, x + cell_w, y + thumb_h + label_h], fill=(38, 38, 38))
        tag = "first" if i == 0 else ("last" if i == n - 1 else "")
        d.text((x + 4, y + thumb_h + 4), f"#{i}  t={i / fps:.3f}s  {tag}", fill=(235, 235, 235), font=f)
    if max(sheet.size) > args.max_edge:
        s = args.max_edge / max(sheet.size)
        sheet = sheet.resize((round(sheet.size[0] * s), round(sheet.size[1] * s)), Image.Resampling.LANCZOS)
    C.save_jpeg(args.out, np.asarray(sheet), quality=85)

    result = {"file": Path(args.clip).name, "frames": n, "fps": round(fps, 4), "duration_s": round(n / fps, 3),
              "seam_similarity": round(seam, 4), "typical_step_similarity": round(typical, 4),
              "seam_ok": bool(ok_seam), "grid": str(args.out), "width": sheet.size[0], "height": sheet.size[1]}
    sys.stdout.write(json.dumps(result, indent=2) + "\n")
    if args.qa:
        stem = Path(args.clip).stem
        # merge under the clip's entry without clobbering video_grade's fields
        p = Path(args.qa) / "video_stats.json"
        data = json.loads(p.read_text()) if p.is_file() else {}
        entry = dict(data.get(stem) or {})
        entry.update({"seam_similarity": result["seam_similarity"], "typical_step_similarity": result["typical_step_similarity"],
                      "seam_ok": result["seam_ok"], "loop_frames": n, "grid": str(args.out)})
        C.update_stats(p, {stem: entry})
        flags = []
        if not ok_seam:
            flags.append({"file": Path(args.clip).name, "code": "loop_seam", "value": result["seam_similarity"],
                          "detail": f"last->first similarity {seam:.3f} vs typical step {typical:.3f}",
                          "hint": "re-run loop_period.py with a different --start, or use --loop pingpong"})
        C.write_flags(args.qa, "frame_grid", [Path(args.clip).name], flags)
    return 0


if __name__ == "__main__":
    sys.exit(main())
