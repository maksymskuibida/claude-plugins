#!/usr/bin/env python3
"""Rearrange an iteration folder into the layout skill-creator's aggregator and viewer read.

    bench_layout.py results/iteration-1

  <name>/<config>/{outputs,grading.json,timing.json}
becomes
  eval-<id>-<name>/eval_metadata.json
  eval-<id>-<name>/<config>/eval_metadata.json
  eval-<id>-<name>/<config>/run-1/{outputs,grading.json,timing.json}
Idempotent: already converted folders are left alone.
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path


def main() -> int:
    it = Path(sys.argv[1])
    for d in sorted(it.iterdir()):
        if not d.is_dir() or d.name.startswith("eval-") or not (d / "eval_metadata.json").is_file():
            continue
        meta = json.loads((d / "eval_metadata.json").read_text())
        target = it / f"eval-{meta['eval_id']}-{d.name}"
        target.mkdir(exist_ok=True)
        shutil.copyfile(d / "eval_metadata.json", target / "eval_metadata.json")
        for cfg in sorted(d.iterdir()):
            if not cfg.is_dir():
                continue
            run = target / cfg.name / "run-1"
            run.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(d / "eval_metadata.json", target / cfg.name / "eval_metadata.json")
            if run.exists():
                shutil.rmtree(run)
            shutil.move(str(cfg), str(run))
        shutil.rmtree(d)
        print(f"{d.name} -> {target.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
