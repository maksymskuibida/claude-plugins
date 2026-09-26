#!/usr/bin/env python3
"""Derive the session look from a grey-card measurement and write session.json.

    calibrate.py --measure work/measure/card.json --out work/session.json
    calibrate.py --from work/session.json --out work/session.json --contrast-strength 3.5
    calibrate.py --from work/session.json --out work/session.json \
        --override dish-0412 exposure_ev=0.3 saturation=0.95

White balance gains make the card neutral in linear light. Exposure is the
multiplier that lands the card at --target-luminance in the *output* JPEG,
i.e. after the tone curve, so changing contrast or shadows here keeps the
card on target. A look changed by hand in session.json does not.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common as C  # noqa: E402


def parse_value(text: str):
    t = text.strip()
    if t.lower() in ("null", "none", ""):
        return None
    if t.lower() in ("true", "false"):
        return t.lower() == "true"
    if t.startswith("[") or t.startswith("{"):
        return json.loads(t)
    if "," in t and all(p.strip().replace(".", "", 1).replace("-", "", 1).isdigit() for p in t.split(",")):
        return [float(p) for p in t.split(",")]
    try:
        return int(t) if t.lstrip("-").isdigit() else float(t)
    except ValueError:
        return t


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--measure", help="measure.py JSON of the grey-card frame")
    ap.add_argument("--from", dest="base", help="existing session.json to start from")
    ap.add_argument("--out", required=True)
    ap.add_argument("--target-luminance", type=float, default=0.48, help="card value in the output, sRGB 0-1")
    ap.add_argument("--neutral-tolerance", type=float, default=1.0, help="allowed |R-G|,|B-G| on the card, /255 (recorded for QA)")
    ap.add_argument("--wb-gains", help="manual R,G,B linear gains (skips the card)")
    ap.add_argument("--exposure", type=float, help="manual linear exposure multiplier (skips the card)")
    ap.add_argument("--exposure-ev", type=float, default=0.0, help="extra stops on top of the derived exposure")
    ap.add_argument("--contrast-strength", type=float)
    ap.add_argument("--contrast-midpoint", type=float)
    ap.add_argument("--shadows", type=float)
    ap.add_argument("--highlights", type=float)
    ap.add_argument("--saturation", type=float)
    ap.add_argument("--crop-ratio", help='e.g. 4:3, 1:1, or "none"')
    ap.add_argument("--straighten-deg", type=float)
    ap.add_argument("--output-long-edge", type=int)
    ap.add_argument("--background", help='#RRGGBB, or "none" for no cutout')
    ap.add_argument("--shadow-blur", type=float)
    ap.add_argument("--shadow-offset", type=float)
    ap.add_argument("--shadow-opacity", type=float)
    ap.add_argument("--override", nargs="+", action="append", metavar=("FILE", "KEY=VALUE"),
                    help="per-file override: FILE key=value [key=value ...]; repeatable")
    ap.add_argument("--clear-overrides", action="store_true")
    ap.add_argument("--note", help="free text stored in provenance")
    args = ap.parse_args()

    session = C.load_session(args.base) if args.base else C.default_session()
    prov = dict(session.get("provenance") or {})
    if not args.base and not args.measure and not (args.wb_gains and args.exposure):
        C.die("need --measure (grey card), or --from, or both --wb-gains and --exposure")

    # look parameters
    if args.contrast_strength is not None:
        session["contrast"]["strength"] = args.contrast_strength
    if args.contrast_midpoint is not None:
        session["contrast"]["midpoint"] = args.contrast_midpoint
    for key in ("shadows", "highlights", "saturation", "straighten_deg", "output_long_edge"):
        val = getattr(args, key)
        if val is not None:
            session[key] = val
    if args.crop_ratio is not None:
        session["crop_ratio"] = None if args.crop_ratio.lower() in ("none", "null", "") else args.crop_ratio
        if session["crop_ratio"] is not None:
            C.parse_ratio(session["crop_ratio"])  # validate
    if args.background is not None:
        if args.background.lower() in ("none", "null", ""):
            session["background"] = None
        else:
            C.parse_colour(args.background)
            session["background"] = "#" + args.background.lstrip("#").upper()
    for key in ("blur", "offset", "opacity"):
        val = getattr(args, f"shadow_{key}")
        if val is not None:
            session["shadow"][key] = val
    if not (0 <= session["highlights"] <= 1) or not (-1 <= session["shadows"] <= 1):
        C.die("shadows must be within -1..1 and highlights within 0..1 (the curve stops being monotonic beyond)")
    if session["contrast"]["strength"] < 0:
        C.die("contrast strength must be >= 0")

    # white balance and exposure
    card = None
    if args.measure:
        m = json.loads(Path(args.measure).read_text())
        if m.get("schema") != "dish-media.measure/1":
            C.die(f"{args.measure} is not a measure.py file")
        card = m
        prov["measure_file"] = str(Path(args.measure))
        prov["card"] = {"mean_linear": m["card_mean_linear"], "mean_srgb255": m["card_mean_srgb255"],
                        "region": m["card_region"], "source": m.get("source")}
    if args.wb_gains:
        gains = [float(v) for v in args.wb_gains.split(",")]
        if len(gains) != 3:
            C.die("--wb-gains wants R,G,B")
        session["wb_gains"] = gains
    elif card is not None:
        r, g, b = card["card_mean_linear"]
        session["wb_gains"] = [round(g / r, 6), 1.0, round(g / b, 6)]

    if args.exposure is not None:
        session["exposure"] = float(args.exposure)
    elif card is not None:
        r, g, b = card["card_mean_linear"]
        card_after_wb = float(g)  # gains make the card (g, g, g)
        wanted_enc = C.invert_tone(args.target_luminance, session)
        wanted_lin = float(C.srgb_to_linear(wanted_enc))
        session["exposure"] = round(wanted_lin / card_after_wb, 6)
    if args.exposure_ev:
        session["exposure"] = round(float(session["exposure"]) * (2.0 ** args.exposure_ev), 6)

    # overrides
    if args.clear_overrides:
        session["overrides"] = {}
    for spec in args.override or []:
        if len(spec) < 2:
            C.die("--override wants FILE followed by at least one key=value")
        name, kvs = C.stem_of(spec[0]), spec[1:]
        ov = dict(session["overrides"].get(name) or {})
        for kv in kvs:
            if "=" not in kv:
                C.die(f"override {kv!r} is not key=value")
            k, v = kv.split("=", 1)
            if k not in C.OVERRIDE_KEYS:
                C.die(f"unknown override key {k!r}; allowed: {', '.join(sorted(C.OVERRIDE_KEYS))}")
            ov[k] = parse_value(v)
        session["overrides"][name] = ov

    # predicted card in the output, for the record and for QA
    import numpy as np
    predicted = None
    if card is not None:
        lin = np.asarray(card["card_mean_linear"], dtype=np.float32)[None, None, :]
        out = C.linear_to_srgb(C.apply_look_linear(lin, session))[0, 0] * 255.0
        predicted = [round(float(v), 2) for v in out]

    prov.update({
        "created": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "script_version": C.SCRIPT_VERSION,
        "targets": {"card_luminance_srgb": args.target_luminance, "neutral_tolerance_255": args.neutral_tolerance},
        "predicted_card_srgb255": predicted,
    })
    if args.note:
        prov["note"] = args.note
    session["provenance"] = prov
    ordered = {"schema": C.SESSION_SCHEMA, "provenance": prov}
    for k in ("wb_gains", "exposure", "contrast", "shadows", "highlights", "saturation", "crop_ratio",
              "straighten_deg", "output_long_edge", "background", "shadow", "overrides"):
        ordered[k] = session[k]
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    C.save_session(args.out, ordered)
    ev = math.log2(float(session["exposure"])) if session["exposure"] > 0 else float("nan")
    C.log(f"wb_gains {session['wb_gains']}  exposure x{session['exposure']} ({ev:+.2f} EV)  "
          f"contrast {session['contrast']}  shadows {session['shadows']}  highlights {session['highlights']}  "
          f"saturation {session['saturation']}")
    if predicted:
        C.log(f"predicted card in output: {predicted} (target {round(args.target_luminance * 255, 1)})")
    C.log(f"overrides: {len(session['overrides'])} -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
