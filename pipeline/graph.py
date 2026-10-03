"""Thin Neo4j driver wrapper: idempotent batched MERGE of nodes and relationships."""
from __future__ import annotations

import math
from typing import Iterable, Sequence

from neo4j import GraphDatabase

from . import config

_driver = None


def driver():
    global _driver
    if _driver is None:
        _driver = GraphDatabase.driver(config.NEO4J_URI, auth=(config.NEO4J_USER, config.NEO4J_PASSWORD))
    return _driver


def run(cypher: str, **params) -> list[dict]:
    with driver().session() as s:
        return [r.data() for r in s.run(cypher, **params)]


def _clean(v):
    # Neo4j rejects NaN-like values poorly and cannot store None inside maps sensibly; drop NaN.
    if isinstance(v, float) and math.isnan(v):
        return None
    if hasattr(v, "item") and not isinstance(v, (list, dict, str)):  # numpy scalar
        try:
            return v.item()
        except Exception:
            return v
    if hasattr(v, "tolist"):  # numpy array
        return v.tolist()
    return v


def _rows(rows) -> list[dict]:
    if hasattr(rows, "to_dict"):
        rows = rows.to_dict("records")
    return [{k: _clean(v) for k, v in r.items()} for r in rows]


def _batches(rows: Sequence, n: int = config.BATCH):
    for i in range(0, len(rows), n):
        yield rows[i : i + n]


def merge_nodes(label: str, key: str, rows, source: str, run_id: str = config.RUN_ID) -> int:
    """MERGE (n:label {key: row[key]}) SET n += row (null values are skipped, not written)."""
    rows = _rows(rows)
    q = f"""
    UNWIND $rows AS row
    MERGE (n:`{label}` {{`{key}`: row.`{key}`}})
    SET n += apoc.map.clean(row, [], [null]), n.source = $source, n.run_id = $run_id
    """
    for b in _batches(rows):
        run(q, rows=b, source=source, run_id=run_id)
    return len(rows)


def merge_rels(
    rel_type: str,
    start_label: str,
    start_key: str,
    end_label: str,
    end_key: str,
    rows,
    source: str,
    start_field: str = "start",
    end_field: str = "end",
    run_id: str = config.RUN_ID,
) -> int:
    """rows: dicts with `start`, `end` (key values) and any relationship properties.

    MATCHes both endpoints (they must already exist) and MERGEs one relationship per pair.
    """
    rows = _rows(rows)
    q = f"""
    UNWIND $rows AS row
    MATCH (a:`{start_label}` {{`{start_key}`: row.`{start_field}`}})
    MATCH (b:`{end_label}` {{`{end_key}`: row.`{end_field}`}})
    MERGE (a)-[r:`{rel_type}`]->(b)
    SET r += apoc.map.clean(apoc.map.removeKeys(row, [$sf, $ef]), [], [null]),
        r.source = $source, r.run_id = $run_id
    """
    for b in _batches(rows):
        run(q, rows=b, source=source, run_id=run_id, sf=start_field, ef=end_field)
    return len(rows)


def delete_by_source(source: str, labels: Iterable[str] = (), rel_types: Iterable[str] = ()) -> None:
    """Wipe what a stage created, so it can be reloaded from clean."""
    for t in rel_types:
        run(f"MATCH ()-[r:`{t}` {{source:$s}}]->() CALL {{ WITH r DELETE r }} IN TRANSACTIONS OF 5000 ROWS", s=source)
    for l in labels:
        run(f"MATCH (n:`{l}` {{source:$s}}) CALL {{ WITH n DETACH DELETE n }} IN TRANSACTIONS OF 5000 ROWS", s=source)


def apply_schema() -> None:
    text = (config.NEO4J_DIR / "schema.cypher").read_text()
    for stmt in [s.strip() for s in text.split(";")]:
        lines = [l for l in stmt.splitlines() if not l.strip().startswith("//")]
        stmt = "\n".join(lines).strip()
        if stmt:
            run(stmt)


def counts() -> dict:
    nodes = run("MATCH (n) RETURN labels(n)[0] AS label, count(*) AS n ORDER BY label")
    rels = run("MATCH ()-[r]->() RETURN type(r) AS type, count(*) AS n ORDER BY type")
    return {"nodes": {r["label"]: r["n"] for r in nodes}, "rels": {r["type"]: r["n"] for r in rels}}
