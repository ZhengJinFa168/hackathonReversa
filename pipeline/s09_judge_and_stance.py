"""S09 LLM judge (both tracks) and Track A stance coding, through OpenRouter (pipeline/llm.py).

Judge
- Input: ECHOED_IN pairs, organisation name hidden. Per track: every verbatim pair plus the top pairs by score,
  up to LLM_MAX_PAIRS_PER_TRACK (2,000).
- Output (JSON schema): llm_relation (implements | partial | contradicts | unrelated), llm_direction
  (strengthens | weakens | neutral: what the target does to the obligations/protections compared with the
  proposal or status quo, in the direction the lobby text asks), overlap_quote (from the target text).
- Tiers: T1 = verbatim and implements; T2 = verbatim, or implements / partial; T3 = everything else.
  'unrelated' pairs stay in the graph with hidden = true.

Track A stance
- Issue codebook below (policy options of the inception impact assessment plus the recurring questions).
- Each 13340 comment is coded on every issue in one call per part (parts of <= ~4,500 words); a stance found
  in any part counts, conflicting parts become 'mixed'.
- Issue.proposal_choice is coded from the proposal text of the issue's articles (Provision nodes) by three
  models (LLM_MODEL, LLM_MODEL_SECOND, LLM_MODEL_HARD) and the majority is kept; disagreements are logged.
  (The plan's "3 seeded runs" give identical answers at temperature 0, and Anthropic endpoints ignore seeds,
  so three model families are the meaningful repeat.) Liability is not in the AI Act: "not_addressed".
- TOOK_POSITION {stance, quote, aligned}.

Usage: python -m pipeline.run --only s09 [--track A|B]; env S09_LIMIT=n caps the judge pairs per track (dry runs).
"""
from __future__ import annotations

import json
import logging
import os
from collections import Counter

import numpy as np
import pandas as pd

from . import config, graph, llm

log = logging.getLogger("s09")
SOURCE = "s09_judge_and_stance"
F = config.FEATURES

# ---------------------------------------------------------------- judge
JUDGE_SYSTEM = ("You compare a passage from a stakeholder's consultation submission with a piece of EU legislative "
                "text. Judge only what the texts say. Answer with JSON matching the schema.")
JUDGE_SCHEMA = {
    "type": "object",
    "properties": {
        "llm_relation": {"type": "string", "enum": ["implements", "partial", "contradicts", "unrelated"]},
        "llm_direction": {"type": "string", "enum": ["strengthens", "weakens", "neutral"]},
        "overlap_quote": {"type": "string"},
    },
    "required": ["llm_relation", "llm_direction", "overlap_quote"],
    "additionalProperties": False,
}
JUDGE_PROMPT = """STAKEHOLDER PASSAGE (consultation submission):
\"\"\"{chunk}\"\"\"

LEGISLATIVE TEXT ({target_kind}):
{target}

Questions:
1. llm_relation: does the legislative text put into effect what the stakeholder passage asks for or proposes?
   - "implements": it does what the passage asks, in substance (wording may differ)
   - "partial": it goes some of the way, or does one of several things asked
   - "contradicts": it does the opposite of what the passage asks
   - "unrelated": it addresses something else, or the overlap is only a shared topic or a common quotation
2. llm_direction: compared with {baseline}, does the legislative text make the rules on AI stricter or more
   protective ("strengthens"), looser or less burdensome ("weakens"), or neither ("neutral")?
3. overlap_quote: the shortest quote (max 30 words) from the LEGISLATIVE TEXT that matches the passage;
   empty string if unrelated."""


def _target_block(track: str, row) -> tuple[str, str, str]:
    if track == "A":
        return "a provision of the Commission's 2021 proposal", f"[{row.target_id}] {row.target_text}", \
            "the situation before this proposal"
    old = row.old_text if isinstance(row.old_text, str) and row.old_text else "(new provision)"
    return ("a European Parliament committee amendment to the 2021 proposal",
            f"Provision: {row.location}\nProposal text: {old}\nAmended text: {row.new_text}\n"
            f"Words the amendment inserts: {row.added_text}", "the Commission's proposal text")


def judge_prompt(track: str, row) -> str:
    kind, target, baseline = _target_block(track, row)
    return JUDGE_PROMPT.format(chunk=row.chunk_text, target_kind=kind, target=target, baseline=baseline)


def judge(track: str, row, model: str | None = None) -> dict:
    kw = dict(system=JUDGE_SYSTEM, max_tokens=config.JUDGE_MAX_TOKENS, tag=f"judge_{track}")
    try:
        return llm.complete(judge_prompt(track, row), JUDGE_SCHEMA, model=model, **kw)
    except llm.LLMError:
        if model is not None:
            raise
        # the main model returned invalid JSON twice: fall back to the hard-case model for this pair
        return {**llm.complete(judge_prompt(track, row), JUDGE_SCHEMA, model=config.LLM_MODEL_HARD, **kw),
                "fallback_model": config.LLM_MODEL_HARD}


def judge_pairs(track: str) -> pd.DataFrame:
    e = pd.read_parquet(config.CACHE / f"s08_echoes_{track}.parquet")
    limit = int(os.getenv("S09_LIMIT", config.LLM_MAX_PAIRS_PER_TRACK))
    pick = pd.concat([e[e.verbatim], e[~e.verbatim].sort_values("score", ascending=False)]).head(limit)
    ch = pd.read_parquet(F / f"{track}_chunks.parquet").set_index("id").text
    pick = pick.assign(chunk_text=pick.chunk_id.map(ch))
    if track == "A":
        t = pd.read_parquet(F / "A_targets.parquet").set_index("id").text
        pick = pick.assign(target_text=pick.target_id.map(t))
    else:
        a = pd.read_parquet(config.CACHE / "s03_amendments.parquet").set_index("am_id")
        pick = pick.join(a[["location", "old_text", "new_text", "added_text"]], on="target_id")
    return pick.reset_index(drop=True)


def tier(verbatim: bool, rel: str | None) -> str:
    if verbatim and rel == "implements":
        return "T1"
    if verbatim or rel in ("implements", "partial"):
        return "T2"
    return "T3"


def run_judge(track: str) -> pd.DataFrame:
    pairs = judge_pairs(track)
    res = llm.batch(list(pairs.itertuples()), lambda r: judge(track, r))
    out = pd.DataFrame(res)
    pairs = pd.concat([pairs, out], axis=1)
    pairs["llm_model"] = config.LLM_MODEL
    ok = pairs.get("error").isna() if "error" in pairs else pd.Series(True, index=pairs.index)
    pairs["tier"] = [tier(v, r if k else None) for v, r, k in zip(pairs.verbatim, pairs.llm_relation, ok)]
    pairs["hidden"] = pairs.llm_relation.eq("unrelated")
    return pairs


def write_judgements(track: str, j: pd.DataFrame) -> None:
    label, key = ("Provision", "unit_id") if track == "A" else ("Amendment", "am_id")
    ok = j[j.llm_relation.notna()] if "llm_relation" in j else j.iloc[0:0]
    rows = ok[["chunk_id", "target_id", "llm_relation", "llm_direction", "overlap_quote", "tier", "hidden", "llm_model"]]
    graph.run(f"""
        UNWIND $rows AS r
        MATCH (:Chunk {{chunk_id: r.chunk_id}})-[e:ECHOED_IN]->(:{label} {{{key}: r.target_id}})
        SET e.llm_relation = r.llm_relation, e.llm_direction = r.llm_direction, e.overlap_quote = r.overlap_quote,
            e.tier = r.tier, e.hidden = r.hidden, e.llm_model = r.llm_model, e.judged_run_id = $run""",
              rows=rows.to_dict("records"), run=config.RUN_ID)


# ---------------------------------------------------------------- Track A issues
ISSUES = [
    dict(issue_id="option", label="Preferred regulatory option (inception impact assessment)",
         choices=["option0_baseline", "option1_soft_law", "option2_voluntary_labelling",
                  "option3a_mandatory_specific_applications", "option3b_mandatory_high_risk",
                  "option3c_mandatory_all_ai", "option4_combination"],
         articles=["Art1", "Art6", "Art52", "Art69"]),
    dict(issue_id="ai_definition", label="Scope of the AI definition",
         choices=["narrow_definition", "broad_definition"], articles=["Art3", "AnnexI"]),
    dict(issue_id="remote_biometric_id", label="Remote biometric identification in public spaces",
         choices=["ban", "restrict_with_exceptions", "regulate_as_high_risk", "no_specific_rules"],
         articles=["Art5"]),
    dict(issue_id="conformity_assessment", label="Who checks high-risk AI before it reaches the market",
         choices=["self_assessment", "third_party_assessment", "mixed"], articles=["Art43"]),
    dict(issue_id="high_risk_list", label="How high-risk AI is defined",
         choices=["closed_list", "open_or_updatable_list", "case_by_case_criteria"],
         articles=["Art6", "Art7", "AnnexIII"]),
    dict(issue_id="sandboxes", label="Regulatory sandboxes",
         choices=["support_sandboxes", "oppose_sandboxes"], articles=["Art53", "Art54"]),
    dict(issue_id="sme_relief", label="Relief for SMEs and start-ups",
         choices=["support_relief", "oppose_relief"], articles=["Art55"]),
    dict(issue_id="liability", label="Changes to liability rules for AI",
         choices=["new_or_adapted_liability_rules", "no_change_to_liability_rules"], articles=[]),
    dict(issue_id="enforcement", label="Enforcement and governance",
         choices=["central_eu_body", "national_authorities", "existing_sectoral_authorities"],
         articles=["Art56", "Art59", "Art63"]),
    dict(issue_id="standards", label="Role of harmonised standards",
         choices=["rely_on_standards", "detailed_rules_in_law"], articles=["Art40", "Art41"]),
]
NA = "not_addressed"


def _stance_schema() -> dict:
    props = {}
    for i in ISSUES:
        props[i["issue_id"]] = {
            "type": "object",
            "properties": {"stance": {"type": "string", "enum": i["choices"] + [NA]}, "quote": {"type": "string"}},
            "required": ["stance", "quote"], "additionalProperties": False}
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}


def _codebook() -> str:
    return "\n".join(f"- {i['issue_id']}: {i['label']}. Choices: {', '.join(i['choices'])}, or {NA}." for i in ISSUES)


STANCE_SYSTEM = ("You code the positions a stakeholder takes in a submission to the European Commission's 2020 "
                 "consultation on an AI regulation (inception impact assessment). Code only positions the text states. "
                 "Answer with JSON matching the schema.")
STANCE_PROMPT = """Codebook (one stance per issue; use "{na}" when the text takes no position):
{codebook}

For each issue give the stance and a supporting quote of at most 30 words from the text (empty if {na}).

SUBMISSION TEXT (part {part} of {parts}):
\"\"\"{text}\"\"\""""
PROPOSAL_PROMPT = """Codebook issue: {label}. Choices: {choices}.

Below is the relevant text of the European Commission's 2021 AI Act proposal. Which choice does the proposal make?
Give the choice, the article you relied on, and a quote of at most 30 words.

{text}"""


def _parts(text: str, max_words: int = 4500) -> list[str]:
    w = text.split()
    return [" ".join(w[i : i + max_words]) for i in range(0, len(w), max_words)] or [""]


def code_comment(text: str) -> dict:
    parts = _parts(text)
    def one(k: int, p: str) -> dict:
        prompt = STANCE_PROMPT.format(na=NA, codebook=_codebook(), part=k + 1, parts=len(parts), text=p)
        kw = dict(system=STANCE_SYSTEM, max_tokens=config.STANCE_MAX_TOKENS, tag="stance")
        try:
            return llm.complete(prompt, _stance_schema(), **kw)
        except llm.LLMError:  # invalid JSON twice: fall back to the hard-case model
            return llm.complete(prompt, _stance_schema(), model=config.LLM_MODEL_HARD, **kw)

    res = [one(k, p) for k, p in enumerate(parts)]
    out = {}
    for i in ISSUES:
        found = [(r[i["issue_id"]]["stance"], r[i["issue_id"]]["quote"]) for r in res if r[i["issue_id"]]["stance"] != NA]
        stances = {s for s, _ in found}
        out[i["issue_id"]] = (("mixed" if len(stances) > 1 else found[0][0]), found[0][1]) if found else (NA, "")
    return out


def proposal_choices() -> pd.DataFrame:
    prov = pd.read_parquet(config.CACHE / "s02_proposal_units.parquet")
    rows = []
    for i in ISSUES:
        if not i["articles"]:
            rows.append(dict(issue_id=i["issue_id"], label=i["label"], proposal_choice=NA, article_ref=None,
                             proposal_quote=None, votes="liability is outside the AI Act", agreement=1.0))
            continue
        txt = "\n\n".join(f"[{a}] " + prov[prov.article == a].full_text.iloc[0][:6000]
                          for a in i["articles"] if (prov.article == a).any() and (prov.unit_id == a).any())
        schema = {"type": "object", "properties": {
            "choice": {"type": "string", "enum": i["choices"]}, "article": {"type": "string"}, "quote": {"type": "string"}},
            "required": ["choice", "article", "quote"], "additionalProperties": False}
        prompt = PROPOSAL_PROMPT.format(label=i["label"], choices=", ".join(i["choices"]), text=txt)
        votes = [llm.complete(prompt, schema, model=m, max_tokens=config.STANCE_MAX_TOKENS, tag="proposal_choice")
                 for m in (config.LLM_MODEL, config.LLM_MODEL_SECOND, config.LLM_MODEL_HARD)]
        c = Counter(v["choice"] for v in votes)
        choice, n = c.most_common(1)[0]
        best = next(v for v in votes if v["choice"] == choice)
        if n < 3:
            log.warning("proposal_choice %s: models disagree %s", i["issue_id"], dict(c))
        rows.append(dict(issue_id=i["issue_id"], label=i["label"], proposal_choice=choice, article_ref=best["article"],
                         proposal_quote=best["quote"], votes=json.dumps(dict(c)), agreement=n / 3))
    return pd.DataFrame(rows)


def run_stance() -> tuple[pd.DataFrame, pd.DataFrame]:
    issues = proposal_choices()
    recs = json.loads((config.ROOT / "WebScraping/feedback/stage1_inception_13340/parsed_feedback_stage1_13340.json").read_text())
    org_of = {r["fid"]: r["org"] for r in graph.run(
        "MATCH (o:Organisation)-[:SUBMITTED]->(c:Comment {track:'A'}) RETURN c.feedback_id AS fid, o.org_id AS org")}
    coded = llm.batch(recs, lambda r: code_comment(r.get("full_text") or ""))
    choice = issues.set_index("issue_id").proposal_choice
    rows = []
    for r, c in zip(recs, coded):
        if "error" in c:
            log.warning("stance failed for %s: %s", r["feedback_id"], c["error"])
            continue
        for iid, (stance, quote) in c.items():
            if stance == NA:
                continue
            rows.append(dict(start=org_of.get(str(r["feedback_id"])), end=iid, feedback_id=str(r["feedback_id"]),
                             stance=stance, quote=quote,
                             aligned=(stance == choice[iid]) if choice[iid] != NA and stance != "mixed" else None))
    return issues, pd.DataFrame(rows)


# ---------------------------------------------------------------- main
def main(track: str | None = None) -> None:
    print(f"   OpenRouter credit: {llm.credits()}")
    for t in ([track] if track else ["A", "B"]):
        j = run_judge(t)
        j.drop(columns=["chunk_text"]).to_parquet(config.CACHE / f"s09_judged_{t}.parquet", index=False)
        write_judgements(t, j)
        err = int(j["error"].notna().sum()) if "error" in j else 0
        print(f"   judge {t}: {len(j)} pairs, {err} errors; relations {j.llm_relation.value_counts().to_dict()}; "
              f"tiers {j.tier.value_counts().to_dict()}")
    if track in (None, "A"):
        issues, pos = run_stance()
        issues.to_parquet(config.CACHE / "s09_issues.parquet", index=False)
        pos.to_parquet(config.CACHE / "s09_positions.parquet", index=False)
        graph.merge_nodes("Issue", "issue_id", issues, SOURCE)
        graph.run("MATCH ()-[r:TOOK_POSITION]->() DELETE r")
        graph.merge_rels("TOOK_POSITION", "Organisation", "org_id", "Issue", "issue_id",
                         pos.dropna(subset=["start"]), SOURCE)
        print(f"   stance: {len(pos)} positions from {pos.feedback_id.nunique()} comments; "
              f"proposal choices {issues.set_index('issue_id').proposal_choice.to_dict()}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
