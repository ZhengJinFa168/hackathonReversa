"""load(name): read a source listed in data_sources/sources.yaml, with placeholder handling.

- status: available   -> the file is read into a DataFrame (or a list of records for JSON dumps)
- status: placeholder -> an empty frame with the schema columns, and "PLACEHOLDER: <name>" is logged
Stages record degraded sources via `degraded()` so the dashboard can show a data-coverage box.
"""
from __future__ import annotations

import io
import json
import logging
from pathlib import Path
from typing import Iterator

import pandas as pd
import yaml

from . import config

log = logging.getLogger("sources")

# Expected columns, checked on load (missing columns are warnings, never errors).
SCHEMAS: dict[str, list[str]] = {
    "lobbyfacts": ["Identification code", "Name", "Members FTE", "Lobbying cost", "Interest represented",
                   "Head office", "EU office", "all EP passes", "Meetings", "Lobbyfacts URL"],
    "integritywatch_meetings": ["epid", "mep", "group", "country", "committees", "role", "dossier", "title",
                                "lobbyists", "location", "date"],
    "integritywatch_register": ["Id", "RegDate", "Cat", "Cat2", "Name", "Country", "People", "FTE", "Accred", "FoI",
                                "Costs", "Meetings", "MeetingsNumVonderleyen1", "MeetingsNumVonderleyen2",
                                "MepMeetingsNum", "MeetingsNumJuncker"],
    "eurlex_proposal": ["unit_id", "kind", "article", "path", "text", "celex"],
    "eurlex_final_act_consolidated": ["unit_id", "kind", "article", "path", "text", "celex",
                                      "mapped_from_unit_id", "map_score"],
}

_DEGRADED: set[str] = set()


def manifest() -> dict:
    return yaml.safe_load(config.SOURCES_YAML.read_text())


def spec(name: str) -> dict:
    s = manifest()["sources"].get(name)
    if s is None:
        raise KeyError(f"unknown source {name!r}")
    return s


def path(name: str, field: str = "path") -> Path | list[Path]:
    v = spec(name)[field]
    return [config.ROOT / p for p in v] if isinstance(v, list) else config.ROOT / v


def degraded() -> list[str]:
    return sorted(_DEGRADED)


def _check(name: str, df: pd.DataFrame) -> pd.DataFrame:
    want = SCHEMAS.get(name)
    if want:
        missing = [c for c in want if c not in df.columns]
        if missing:
            log.warning("%s: missing columns %s", name, missing)
    return df


def _empty(name: str) -> pd.DataFrame:
    _DEGRADED.add(name)
    log.warning("PLACEHOLDER: %s", name)
    return pd.DataFrame(columns=SCHEMAS.get(name, []))


def iter_zst_json(p: Path, needle: str | None = None) -> Iterator[dict]:
    """Stream records from a ParlTrack .json.zst dump (one JSON array, one record per line,
    each line starting with '[' or ','). `needle` filters raw lines before json.loads (fast)."""
    import zstandard

    with open(p, "rb") as fh:
        reader = io.TextIOWrapper(zstandard.ZstdDecompressor().stream_reader(fh), encoding="utf-8")
        for line in reader:
            line = line.strip()
            if not line or line in ("[", "]"):
                continue
            if needle is not None and needle not in line:
                continue
            yield json.loads(line[1:] if line[0] in "[," else line)


def load(name: str):
    s = spec(name)
    if s.get("status") != "available":
        return _empty(name)
    p = path(name)
    if isinstance(p, list):
        p = p[0]
    if not p.exists():
        log.warning("%s: %s not found", name, p)
        return _empty(name)
    if name == "integritywatch_meetings":
        df = pd.read_csv(p, sep=";", encoding="utf-8-sig", dtype=str)
        df["date"] = pd.to_datetime(df["date"], format="%d/%m/%y", errors="coerce")
        return _check(name, df)
    if p.suffix == ".csv":
        df = pd.read_csv(p, dtype=str, keep_default_na=False, na_values=[""])
        if df.empty and name in SCHEMAS:  # header-only file = placeholder by content
            _DEGRADED.add(name)
            log.warning("PLACEHOLDER (header only): %s", name)
        return _check(name, df)
    if p.name.endswith(".json.zst"):
        return iter_zst_json(p)
    if p.suffix == ".json":
        return json.loads(p.read_text())
    raise ValueError(f"don't know how to load {p}")


def raw_feedback(pub_id: int) -> list[dict]:
    """Raw Have Your Say JSON (carries trNumber, which the parsed file lacks)."""
    hits = sorted((config.ROOT / "WebScraping" / "feedback").glob(f"*_{pub_id}/raw_feedback_*_{pub_id}.json"))
    return json.loads(hits[0].read_text()) if hits else []
