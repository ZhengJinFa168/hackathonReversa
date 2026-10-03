"""S00 sanity check: sources, counts, schema checks, Neo4j reachable. Warns only; never blocks."""
from __future__ import annotations

import json
import logging
import re
from collections import Counter

from . import config, sources

log = logging.getLogger("s00")
WARN: list[str] = []


def warn(msg: str) -> None:
    WARN.append(msg)
    print(f"  WARN  {msg}")


def ok(msg: str) -> None:
    print(f"  ok    {msg}")


def check_manifest() -> None:
    print("\n[sources.yaml]")
    for name, s in sources.manifest()["sources"].items():
        status = s.get("status")
        paths = []
        for f in ("path", "raw"):
            v = s.get(f)
            if v:
                paths += v if isinstance(v, list) else [v]
        missing = [p for p in paths if not (config.ROOT / p).exists()]
        tag = "available" if status == "available" else "PLACEHOLDER"
        line = f"{name:32s} {tag}"
        if missing:
            warn(f"{line}  missing: {missing}")
        else:
            ok(line)


def check_feedback() -> None:
    print("\n[Have Your Say feedback]")
    for track, pub in config.PUB_IDS.items():
        recs = sources.load(f"hys_feedback_{pub}")
        ids = [r["feedback_id"] for r in recs]
        empty = sum(1 for r in recs if not (r.get("full_text") or "").strip())
        types = Counter(r.get("user_type") for r in recs)
        ok(f"{pub} (track {track}): {len(recs)} contributions, types {dict(types.most_common())}")
        if len(ids) != len(set(ids)):
            warn(f"{pub}: duplicate feedback_id")
        if recs and empty / len(recs) >= 0.10:
            warn(f"{pub}: {empty}/{len(recs)} empty texts (>=10%)")
        else:
            ok(f"{pub}: {empty} empty texts")
        raw = sources.raw_feedback(pub)
        tr = sum(1 for r in raw if re.search(r"\d{6,15}-\d{2}", str(r.get("trNumber") or "")))
        ok(f"{pub}: {tr}/{len(raw)} raw contributions carry a parseable trNumber")


def check_parltrack() -> None:
    print("\n[ParlTrack]  (streams ~120 MB; takes a moment)")
    needle = json.dumps(config.PROCEDURE)
    n_comm = sum(1 for r in sources.iter_zst_json(sources.path("parltrack_amendments"), needle)
                 if r.get("reference") == config.PROCEDURE)
    (ok if n_comm == 4852 else warn)(f"committee amendments for {config.PROCEDURE}: {n_comm} (plan expects 4,852)")
    plen = [r for r in sources.iter_zst_json(sources.path("parltrack_plenary_amendments"), needle)
            if r.get("reference") == config.PROCEDURE]
    n_rep = sum(1 for r in plen if str(r.get("id", "")).startswith("A9-0188/2023-"))
    (ok if n_rep == 771 else warn)(f"plenary amendments for {config.PROCEDURE}: {len(plen)}, "
                                   f"report amendments A9-0188/2023-*: {n_rep} (plan expects 771)")


def check_registers() -> None:
    print("\n[LobbyFacts / Integrity Watch]")
    lf = sources.load("lobbyfacts")
    ok(f"LobbyFacts: {len(lf)} rows, {lf['Identification code'].nunique()} unique IDs")
    reg = sources.load("integritywatch_register")
    if reg["Id"].duplicated().any():
        warn(f"Integrity Watch register: {reg['Id'].duplicated().sum()} duplicate Ids")
    else:
        ok(f"Integrity Watch register: {len(reg)} rows, Ids unique")
    m = sources.load("integritywatch_meetings")
    bad = m["date"].isna().sum()
    years = Counter(m["date"].dropna().dt.year)
    (warn if bad else ok)(f"Integrity Watch meetings: {len(m)} rows, {bad} unparseable dates, by year {dict(sorted(years.items()))}")
    if len(m) != 6123:
        warn(f"meetings row count {len(m)} != 6,123")
    outside = {y: n for y, n in years.items() if y not in (2020, 2024)}
    if outside:
        warn(f"meetings outside 2020/2024 {outside}: coverage changed, update labels")


def check_eurlex() -> None:
    print("\n[EUR-Lex units]")
    for name in ("eurlex_proposal", "eurlex_final_act_consolidated"):
        df = sources.load(name)
        (ok if len(df) else warn)(f"{name}: {len(df)} units" + ("" if len(df) else " (header-only; run S02)"))


def check_neo4j() -> None:
    print("\n[Neo4j]")
    try:
        from . import graph

        v = graph.run("CALL dbms.components() YIELD name, versions, edition RETURN name, versions[0] AS v, edition")
        ok(f"reachable at {config.NEO4J_URI}: {v[0]}")
        procs = graph.run("SHOW PROCEDURES YIELD name WHERE name STARTS WITH 'apoc.' OR name STARTS WITH 'gds.' "
                          "RETURN split(name,'.')[0] AS p, count(*) AS n")
        have = {r["p"]: r["n"] for r in procs}
        for p in ("apoc", "gds"):
            (ok if have.get(p) else warn)(f"plugin {p}: {have.get(p, 0)} procedures")
        graph.apply_schema()
        cons = graph.run("SHOW CONSTRAINTS YIELD name RETURN count(*) AS n")[0]["n"]
        ok(f"schema.cypher applied: {cons} constraints")
        ok(f"graph counts: {graph.counts()}")
    except Exception as e:  # noqa: BLE001
        warn(f"Neo4j not reachable: {type(e).__name__}: {e}")


def main(skip_parltrack: bool = False) -> int:
    print(f"S00 sanity check  run_id={config.RUN_ID}")
    check_manifest()
    check_feedback()
    check_registers()
    check_eurlex()
    if not skip_parltrack:
        check_parltrack()
    check_neo4j()
    print(f"\n{len(WARN)} warning(s).")
    return 0


if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    sys.exit(main(skip_parltrack="--fast" in sys.argv))
