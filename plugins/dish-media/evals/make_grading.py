#!/usr/bin/env python3
"""Write grading.json for one run from grade_run.py plus manual verdicts.

    make_grading.py <eval-name> <run-dir> [--manual '{"key": [true, "evidence"], ...}']

Assertion order follows eval_metadata.json; KEYS maps each to a grade_run
key or to a manual key supplied on the command line.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
KEYS = {
    "session-with-card": ["graded_all_11", "delivery_format", "white_balance", "loops_format", "loops_seamless", "loops_one_revolution",
                          "contact_sheets", "qa_report", "manual:names_shots_to_redo", "originals_untouched"],
    "no-card-mixed": ["all_graded_or_excluded", "cutouts_on_cream", "soft_shadow", "no_card_explained", "interiors_identified",
                      "cross_session_reported", "delivered_set_even", "contact_sheets", "matte_quality_assessed", "originals_untouched"],
    "loops-and-hdr": ["four_loops_format", "moon_swift_seamless", "hdr_tonemapped", "session_look_applied", "al92_ambiguity_handled",
                      "frame_grids", "seam_scores_recorded", "originals_untouched"],
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("eval_name")
    ap.add_argument("run_dir")
    ap.add_argument("--manual", default="{}")
    args = ap.parse_args()
    run = Path(args.run_dir)
    meta = json.loads((run.parent / "eval_metadata.json").read_text())
    manual = json.loads(args.manual)
    r = subprocess.run([sys.executable, str(HERE / "grade_run.py"), args.eval_name, str(run)], capture_output=True, text=True)
    auto = json.loads(r.stdout)
    exps = []
    for text, key in zip(meta["assertions"], KEYS[args.eval_name]):
        if key.startswith("manual:"):
            v = manual.get(key.split(":", 1)[1])
            if v is None:
                exps.append({"text": text, "passed": False, "evidence": "no manual verdict supplied"})
            else:
                exps.append({"text": text, "passed": bool(v[0]), "evidence": v[1]})
        else:
            a = auto[key]
            exps.append({"text": text, "passed": bool(a["passed"]), "evidence": a["evidence"]})
    passed = sum(1 for e in exps if e["passed"])
    timing = json.loads((run / "timing.json").read_text()) if (run / "timing.json").is_file() else {}
    out = {
        "expectations": exps,
        "summary": {"passed": passed, "failed": len(exps) - passed, "total": len(exps), "pass_rate": round(passed / len(exps), 3)},
        "execution_metrics": {"tool_calls": {}, "total_tool_calls": timing.get("tool_uses", 0), "total_steps": 0, "errors_encountered": 0,
                              "output_chars": sum(p.stat().st_size for p in (run / "outputs").rglob("*.md")), "transcript_chars": 0},
        "timing": {"executor_duration_seconds": timing.get("total_duration_seconds", 0), "total_duration_seconds": timing.get("total_duration_seconds", 0)},
        "claims": [],
        "user_notes_summary": {"uncertainties": [], "needs_review": [], "workarounds": []},
    }
    (run / "grading.json").write_text(json.dumps(out, indent=2) + "\n")
    print(f"{run.parent.name}/{run.name}: {passed}/{len(exps)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
