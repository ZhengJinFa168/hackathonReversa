"""Local proxy for the live demo: python -m pipeline.serve   (http://localhost:8765)

The dashboard page never sees the OpenRouter key; it calls this proxy, which holds it (from .env).

POST /judge {"track": "A"|"B", "chunk_id": ..., "target_id": ...}
    Re-judges one pair with the S09 prompt and streams the model's JSON answer as plain text chunks.
    The pair texts are looked up here from the S09/S05 caches, so the page only sends ids.
POST /ask {"question": "..."}
    Ask the graph: the LLM writes one read-only Cypher query from the schema, the proxy checks it is
    read-only, runs it on Neo4j (LIMIT 50) and returns {"cypher", "columns", "rows"}.
GET /health -> {"ok": true}
"""
from __future__ import annotations

import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pandas as pd

from . import config, graph, llm
from . import s09_judge_and_stance as s09

PORT = 8765
_pairs: dict[str, pd.DataFrame] = {}

SCHEMA_TEXT = """Neo4j graph about lobbying on the EU AI Act (procedure 2021/0106(COD)).
Nodes (key): Organisation(org_id; name, user_type in [COMPANY, BUSINESS_ASSOCIATION, NGO, TRADE_UNION,
CONSUMER_ORGANISATION], country, lobbying_cost_eur, fte, ep_passes_all, ec_meetings_vdl1, mep_meetings_all,
influence_b, coalition), Comment(feedback_id; track 'A' or 'B', date), Chunk(chunk_id; text),
Provision(unit_id like 'Art5(1)(d)', 'Rec27', 'AnnexIII(1)(5)(b)'; text, article), Amendment(am_id; location,
added_text, committee, survived, reached_law, survival_score), AdoptedAmendment(adopted_id; text),
LegalUnit(law_unit_id like '2024/1689:Art5(1)(h)'; text), MEP(mep_id; name, country, iw_group),
Group(group_id in [PPE, S&D, RE, Verts/ALE, ID, ECR, GUE/NGL, NA]), Committee(code), Issue(issue_id; proposal_choice).
Relationships: (Organisation)-[:SUBMITTED]->(Comment), (Chunk)-[:PART_OF]->(Comment),
(Comment)-[:CITES]->(Provision), (Amendment)-[:AMENDS]->(Provision), (MEP)-[:TABLED {weight, is_lead_author}]->(Amendment),
(MEP)-[:MEMBER_OF]->(Group), (Amendment)-[:IN_COMMITTEE]->(Committee),
(Amendment)-[:SURVIVED_AS {score}]->(AdoptedAmendment), (Provision|AdoptedAmendment)-[:BECAME]->(LegalUnit),
(Chunk)-[:ECHOED_IN {score, verbatim, tier in [T1,T2,T3], llm_relation, llm_direction, overlap_quote, hidden,
carrier_met}]->(Provision|Amendment), (Organisation)-[:MET_WITH {date, title, period, ai_related, mep_role}]->(MEP),
(Organisation)-[:TOOK_POSITION {stance, aligned}]->(Issue), (Chunk)-[:SIMILAR_TO]->(Chunk).
Default to echoes with tier IN ['T1','T2'] and NOT coalesce(hidden,false)."""
CYPHER_SCHEMA = {"type": "object", "properties": {"cypher": {"type": "string"}}, "required": ["cypher"],
                 "additionalProperties": False}
WRITE_RE = re.compile(r"\b(CREATE|MERGE|DELETE|DETACH|SET|REMOVE|DROP|LOAD\s+CSV|FOREACH)\b|CALL\s+(apoc|gds|db|dbms)\.", re.I)


def pair(track: str, chunk_id: str, target_id: str):
    if track not in _pairs:
        _pairs[track] = s09.judge_pairs(track)  # same rows and texts the batch judge used
    p = _pairs[track]
    hit = p[(p.chunk_id == chunk_id) & (p.target_id == target_id)]
    return next(hit.itertuples()) if len(hit) else None


class Handler(BaseHTTPRequestHandler):
    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def _json(self, code: int, obj) -> None:
        body = json.dumps(obj, default=str).encode()
        self.send_response(code)
        self._cors()
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/health":
            return self._json(200, {"ok": True, "model": config.LLM_MODEL})
        self._json(404, {"error": "not found"})

    def do_POST(self):
        try:
            req = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        except json.JSONDecodeError:
            return self._json(400, {"error": "bad json"})
        if self.path == "/judge":
            return self.judge(req)
        if self.path == "/ask":
            return self.ask(req)
        self._json(404, {"error": "not found"})

    def judge(self, req):
        row = pair(req.get("track", "B"), req.get("chunk_id"), req.get("target_id"))
        if row is None:
            return self._json(404, {"error": "pair not in the judged set"})
        self.send_response(200)
        self._cors()
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        try:
            for piece in llm.stream(s09.judge_prompt(req.get("track", "B"), row), s09.JUDGE_SCHEMA,
                                    system=s09.JUDGE_SYSTEM, tag="live_judge"):
                self.wfile.write(piece.encode())
                self.wfile.flush()
        except Exception as e:  # noqa: BLE001 - the page falls back to the cached verdict
            self.wfile.write(f"\n[error: {type(e).__name__}]".encode())

    def ask(self, req):
        q = (req.get("question") or "").strip()
        if not q:
            return self._json(400, {"error": "empty question"})
        try:
            ans = llm.complete(f"{SCHEMA_TEXT}\n\nWrite ONE read-only Cypher query (no writes, no procedure calls) "
                               f"that answers: {q}\nReturn at most 50 rows with readable column names.",
                               CYPHER_SCHEMA, max_tokens=600, tag="ask_graph")
            cypher = ans["cypher"].strip().rstrip(";")
            if WRITE_RE.search(cypher):
                return self._json(400, {"error": "query is not read-only", "cypher": cypher})
            if not re.search(r"\bLIMIT\s+\d+", cypher, re.I):
                cypher += " LIMIT 50"
            rows = graph.run(cypher)
            return self._json(200, {"cypher": cypher, "columns": list(rows[0]) if rows else [], "rows": rows[:50]})
        except Exception as e:  # noqa: BLE001
            return self._json(500, {"error": f"{type(e).__name__}: {e}"})

    def log_message(self, fmt, *args):
        print("serve:", fmt % args)


def main() -> None:
    print(f"Live demo proxy on http://localhost:{PORT} (model {config.LLM_MODEL}); Ctrl-C to stop")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
