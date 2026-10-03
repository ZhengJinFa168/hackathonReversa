"""Pipeline runner.

    python -m pipeline.run                      # every implemented stage, both tracks
    python -m pipeline.run --from s03 --track B
    python -m pipeline.run --only s00

Each stage module exposes main(track: str | None) (S00 ignores track). Stages that are not
implemented yet are skipped with a note, so the runner works while the plan is built step by step.
"""
from __future__ import annotations

import argparse
import importlib
import logging
import time

from . import config

STAGES = [
    "s00_check", "s01_feedback", "s02_legal_texts", "s03_amendments", "s04_survival", "s05_features",
    "s06_orgs", "s06b_meetings", "s07_match", "s08_calibrate", "s09_judge_and_stance", "s10_analyse", "s10b_reach_model",
    "s11_export_dashboard", "s11b_demo", "s12_eval",
]


def _select(frm: str | None, only: str | None) -> list[str]:
    def idx(prefix):
        for i, s in enumerate(STAGES):
            if s.split("_")[0] == prefix.lower() or s == prefix:
                return i
        raise SystemExit(f"unknown stage {prefix}")

    if only:
        return [STAGES[idx(o)] for o in only.split(",")]
    return STAGES[idx(frm):] if frm else STAGES


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="frm")
    ap.add_argument("--only")
    ap.add_argument("--track", choices=["A", "B"])
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    print(f"run_id={config.RUN_ID} track={a.track or 'A+B'}")
    for name in _select(a.frm, a.only):
        try:
            mod = importlib.import_module(f"pipeline.{name}")
        except ModuleNotFoundError as e:
            if e.name == f"pipeline.{name}":
                print(f"-- {name}: not implemented yet, skipped")
                continue
            raise
        t = time.time()
        print(f"== {name}")
        mod.main() if name == "s00_check" else mod.main(track=a.track)
        print(f"   {name} done in {time.time() - t:.1f}s")


if __name__ == "__main__":
    main()
