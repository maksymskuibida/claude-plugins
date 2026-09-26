#!/usr/bin/env python3
"""Find the turntable revolution period in a clip.

    loop_period.py raw/video/dish-0412.mov [--start 1.0] [--min-period 4] [--max-period 20]

Every frame after start+min-period is compared with the frame at --start
(downscaled greyscale, normalised cross-correlation). The best match is
one revolution later. Prints JSON. A dish with 2-fold symmetry (a plain
round plate, two identical items) can match at half a turn; the JSON
warns when the half-period score is nearly as good as the best.

The JSON also says which way the dish turns on screen (`direction`: cw or
ccw, from dense optical flow around the frame centre) so every loop on a
menu can be checked to turn the same way.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common as C  # noqa: E402


def read_frames(path: Path, width: int = 160, flow_width: int = 320):
    """Normalised 160 px vectors for matching, plus 320 px greys for optical flow."""
    import cv2
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        C.die(f"cannot open {path}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    frames, greys = [], []
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        g = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        h = max(8, round(g.shape[0] * width / g.shape[1]))
        s = cv2.resize(g, (width, h), interpolation=cv2.INTER_AREA).astype(np.float32)
        s -= s.mean()
        n = float(np.linalg.norm(s))
        frames.append((s / n).ravel() if n > 0 else s.ravel())
        fh = max(8, round(g.shape[0] * flow_width / g.shape[1]))
        greys.append(cv2.resize(g, (flow_width, fh), interpolation=cv2.INTER_AREA))
    cap.release()
    if not frames:
        C.die(f"no frames decoded from {path}")
    return float(fps), np.stack(frames), greys


def rotation_direction(greys: list, start: int, count: int, step: int = 3) -> tuple[str, float]:
    """Sense of rotation on screen from the mean tangential optical flow
    around the frame centre: ("cw" | "ccw" | "none", consistency 0..1).

    Image y runs downward, so a positive tangential component (angle
    increasing) is clockwise as displayed. Only pixels that move are
    counted, and only inside the central disc where the dish sits."""
    import cv2
    h, w = greys[0].shape
    cx, cy = w / 2.0, h / 2.0
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    dx, dy = xx - cx, yy - cy
    r = np.hypot(dx, dy) + 1e-6
    disc = (r < 0.45 * min(w, h)) & (r > 0.05 * min(w, h))
    signs = []
    end = min(len(greys) - step, start + count)
    for i in range(start, end, step):
        flow = cv2.calcOpticalFlowFarneback(greys[i], greys[i + step], None, 0.5, 3, 21, 3, 5, 1.2, 0)
        u, v = flow[..., 0], flow[..., 1]
        mag = np.hypot(u, v)
        moving = disc & (mag > 0.3)
        if moving.sum() < 50:
            continue
        tangential = (-dy * u + dx * v) / r          # + means angle increasing = clockwise on screen
        signs.append(float(np.sign(tangential[moving].mean())))
    if not signs:
        return "none", 0.0
    s = float(np.mean(signs))
    if abs(s) < 0.5:
        return "none", round(abs(s), 3)
    return ("cw" if s > 0 else "ccw"), round(abs(s), 3)


def detect(path, start: float = 1.0, min_period: float = 4.0, max_period: float = 20.0) -> dict:
    path = Path(path)
    fps, vecs, greys = read_frames(path)
    n = len(vecs)
    ref = int(round(start * fps))
    if ref >= n:
        C.die(f"--start {start}s is past the end of {path.name} ({n / fps:.2f}s)")
    lo = ref + int(round(min_period * fps))
    hi = min(n - 1, ref + int(round(max_period * fps)))
    warnings = []
    result = {"file": path.name, "fps": round(fps, 4), "frames": n, "duration_s": round(n / fps, 3),
              "reference_frame": ref, "reference_time_s": round(ref / fps, 3)}
    steps = np.einsum("ij,ij->i", vecs[1:], vecs[:-1])
    result["typical_step_similarity"] = round(float(np.median(steps)), 4) if len(steps) else None
    if lo > hi:
        warnings.append(f"clip too short: needs at least {start + min_period:.1f}s to look for a period")
        result.update({"period_frames": None, "period_s": None, "score": None, "warnings": warnings})
        return result
    scores = vecs[lo:hi + 1] @ vecs[ref]
    best = int(np.argmax(scores))
    pf = lo - ref + best
    score = float(scores[best])
    refined = float(pf)
    if 0 < best < len(scores) - 1:
        a, b, c = float(scores[best - 1]), score, float(scores[best + 1])
        denom = a - 2 * b + c
        if abs(denom) > 1e-9:
            refined = pf + 0.5 * (a - c) / denom
    half = pf // 2
    half_score = float(vecs[ref + half] @ vecs[ref]) if half >= 1 else None
    if score < 0.6:
        warnings.append(f"best match is weak ({score:.2f}); is the turntable turning, and does one turn fit in the clip?")
    if half_score is not None and half_score > 0.97 * score and half_score > 0.8:
        warnings.append(f"half-period frame matches almost as well ({half_score:.2f} vs {score:.2f}); the dish may be 2-fold symmetric, check the loop")
    direction, consistency = rotation_direction(greys, ref, pf)
    result.update({
        "period_frames": pf, "period_s": round(pf / fps, 4), "period_s_refined": round(refined / fps, 4),
        "score": round(score, 4), "score_half_period": round(half_score, 4) if half_score is not None else None,
        "revolution_start_s": round(ref / fps, 4), "revolution_end_s": round((ref + pf) / fps, 4),
        "direction": direction, "direction_consistency": consistency,
        "warnings": warnings,
    })
    return result


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("clip")
    ap.add_argument("--start", type=float, default=1.0, help="reference time in seconds (after the turntable is up to speed)")
    ap.add_argument("--min-period", type=float, default=4.0)
    ap.add_argument("--max-period", type=float, default=20.0)
    ap.add_argument("--out", help="write the JSON here as well as stdout")
    args = ap.parse_args()
    r = detect(args.clip, args.start, args.min_period, args.max_period)
    text = json.dumps(r, indent=2) + "\n"
    sys.stdout.write(text)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text)
    for w in r.get("warnings", []):
        C.log(f"warning: {w}")
    return 0 if r.get("period_frames") else 1


if __name__ == "__main__":
    sys.exit(main())
