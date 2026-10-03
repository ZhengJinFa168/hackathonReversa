#!/usr/bin/env python3
"""
load_all.py

Master pipeline loader that executes all ingestion steps in order:
1. Applies schema constraints and indexes (apply_schema.py)
2. Ingests consultations (Feedback Stage 1 & 2), Chunks, Organizations & LobbyFacts (load_data.py)
3. Ingests ParlTrack data: PoliticalGroups, MEPs, Amendments, LawProvisions (load_parltrack.py)
4. Ingests Integrity Watch MEP meetings (load_meetings.py)
"""

import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

def run_step(script_name: str):
    print(f"\n{'='*70}")
    print(f"RUNNING: {script_name}")
    print(f"{'='*70}")
    script_path = HERE / script_name
    res = subprocess.run([sys.executable, str(script_path)])
    if res.returncode != 0:
        print(f"ERROR: {script_name} failed with code {res.returncode}")
        sys.exit(res.returncode)

def main():
    print("STARTING COMPLETE NEO4J GRAPH INGESTION PIPELINE")
    run_step("apply_schema.py")
    run_step("load_data.py")
    run_step("load_parltrack.py")
    run_step("load_meetings.py")
    print(f"\n{'='*70}")
    print("ALL DATA SOURCES SUCCESSFULLY INGESTED INTO NEO4J!")
    print(f"{'='*70}")

if __name__ == "__main__":
    main()
