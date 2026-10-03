"""S10 Analysis: run the named queries in neo4j/queries/*.cypher (Cypher + GDS) and the statistics that need
Python, writing everything to data/results/.

Each .cypher file may hold several statements separated by ';' (e.g. drop graph, project, stream); the result
of the last statement is exported as data/results/<file name>.csv.

Python statistics on top of the query results:
- money_vs_influence.csv: Spearman correlation of the influence score with lobbying cost, FTE, EP passes,
  Commission meetings 2019-24 and MEP meetings, by user type, with 95% bootstrap intervals; repeated on
  register-number matches only (sensitivity check). Label: 2026 register figures.
- track_a_alignment.csv: share of positions matching the proposal's choice by user type, against two baselines:
  a uniformly random choice per issue, and a permutation of stances within each issue (95% interval; per user
  type only, since a within-issue permutation leaves the overall share unchanged).
- access_vs_echo.csv: share of echoed (organisation, amendment) pairs where a tabling MEP has a recorded
  meeting with the organisation, against the same organisations paired with random AI Act tabling MEPs that
  appear in the meetings data. Label: meetings recorded in 2020 and Jun-Oct 2024 only.
"""
from __future__ import annotations

import json
import logging

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

from . import config, graph

log = logging.getLogger("s10")
RNG = np.random.default_rng(10)
R = config.RESULTS


def statements(text: str) -> list[str]:
    body = "\n".join(l for l in text.splitlines() if not l.strip().startswith("//"))
    return [s.strip() for s in body.split(";") if s.strip()]


def run_queries() -> dict[str, pd.DataFrame]:
    out = {}
    for f in sorted(config.QUERIES.glob("*.cypher")):
        res = None
        for st in statements(f.read_text()):
            res = graph.run(st)
        df = pd.DataFrame(res or [])
        df.to_csv(R / f"{f.stem}.csv", index=False)
        out[f.stem] = df
        log.info("%s: %d rows", f.stem, len(df))
    return out


# ---------------------------------------------------------------- money vs influence
METRICS = ["lobbying_cost_eur", "fte", "ep_passes_all", "ec_meetings_vdl1", "mep_meetings_all"]


def _spearman_ci(x: np.ndarray, y: np.ndarray, n_boot: int = 1000) -> tuple[float, float, float]:
    rho = spearmanr(x, y).statistic
    boots = []
    for _ in range(n_boot):
        i = RNG.integers(0, len(x), len(x))
        if len(set(x[i])) > 1 and len(set(y[i])) > 1:
            boots.append(spearmanr(x[i], y[i]).statistic)
    lo, hi = np.percentile(boots, [2.5, 97.5]) if boots else (np.nan, np.nan)
    return float(rho), float(lo), float(hi)


def money_vs_influence(orgs: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for sample, df in [("all matches", orgs), ("register-number matches only", orgs[orgs.match_method == "tr_number"])]:
        for ut, g in [("ALL", df)] + list(df.groupby("user_type")):
            for m in METRICS:
                d = g[[m, "influence"]].dropna()
                if len(d) < 8:
                    continue
                rho, lo, hi = _spearman_ci(d[m].to_numpy(float), d.influence.to_numpy(float))
                rows.append(dict(sample=sample, user_type=ut, metric=m, n=len(d), spearman_rho=round(rho, 3),
                                 ci_low=round(lo, 3), ci_high=round(hi, 3), note="2026 register figures"))
    return pd.DataFrame(rows)


# ---------------------------------------------------------------- Track A alignment
def track_a_alignment(pos: pd.DataFrame, n_perm: int = 1000) -> pd.DataFrame:
    from .s09_judge_and_stance import ISSUES

    k = {i["issue_id"]: len(i["choices"]) for i in ISSUES}
    p = pos[pos.aligned.notna()].copy()
    p["aligned"] = p.aligned.astype(bool)
    p["random_choice_p"] = p.issue.map(lambda i: 1 / k[i])
    rows = []
    groups = [("ALL", p)] + list(p.groupby("user_type"))
    perm = {ut: [] for ut, _ in groups}
    for _ in range(n_perm):
        q = p.copy()
        q["stance"] = q.groupby("issue").stance.transform(lambda s: s.sample(frac=1, random_state=int(RNG.integers(1e9))).values)
        q["al"] = q.stance == q.proposal_choice
        for ut, _g in groups:
            sel = q if ut == "ALL" else q[q.user_type == ut]
            perm[ut].append(sel.al.mean())
    for ut, g in groups:
        lo, hi = np.percentile(perm[ut], [2.5, 97.5])
        if ut == "ALL":  # permuting within issues keeps the overall count fixed: no baseline for ALL
            lo = hi = np.nan
            perm[ut] = [np.nan]
        rows.append(dict(user_type=ut, positions=len(g), organisations=g.org_id.nunique(),
                         aligned_share=round(g.aligned.mean(), 3),
                         random_choice_baseline=round(g.random_choice_p.mean(), 3),
                         permutation_mean=round(float(np.mean(perm[ut])), 3),
                         permutation_ci_low=round(float(lo), 3), permutation_ci_high=round(float(hi), 3)))
    by_issue = (p.groupby("issue").agg(positions=("aligned", "size"), aligned_share=("aligned", "mean"),
                                       proposal_choice=("proposal_choice", "first")).round(3).reset_index())
    by_issue.to_csv(R / "track_a_alignment_by_issue.csv", index=False)
    return pd.DataFrame(rows)


# ---------------------------------------------------------------- access vs echo
def access_vs_echo(pairs: pd.DataFrame, n_rep: int = 200) -> pd.DataFrame:
    met = graph.run("MATCH (o:Organisation)-[:MET_WITH]->(m:MEP) RETURN DISTINCT o.org_id AS org, m.mep_id AS mep")
    met_set = {(r["org"], r["mep"]) for r in met}
    pool = [r["id"] for r in graph.run(
        "MATCH (m:MEP)-[:TABLED]->(:Amendment) WHERE EXISTS { (m)<-[:MET_WITH]-() } "
        "OR EXISTS { MATCH (m) WHERE m.iw_group IS NOT NULL } RETURN DISTINCT m.mep_id AS id")]
    pool = np.array(pool)
    rows = []
    for ut, g in [("ALL", pairs)] + list(pairs.groupby("user_type")):
        actual = g.carrier_met.mean()
        sims = []
        for _ in range(n_rep):
            hit = 0
            for o, t in zip(g.org_id, g.tablers):
                draw = RNG.choice(pool, size=min(len(t), len(pool)), replace=False)
                hit += any((o, int(m)) in met_set for m in draw)
            sims.append(hit / len(g))
        lo, hi = np.percentile(sims, [2.5, 97.5])
        rows.append(dict(user_type=ut, echoed_pairs=len(g), carrier_met_share=round(actual, 3),
                         random_tablers_mean=round(float(np.mean(sims)), 3), random_ci_low=round(float(lo), 3),
                         random_ci_high=round(float(hi), 3), baseline_pool_meps=len(pool),
                         note="meetings recorded in 2020 and Jun-Oct 2024 only"))
    return pd.DataFrame(rows)


def main(track: str | None = None) -> None:
    q = run_queries()
    mvi = money_vs_influence(q["12_org_money"])
    mvi.to_csv(R / "money_vs_influence.csv", index=False)
    ta = track_a_alignment(q["08_track_a_positions"])
    ta.to_csv(R / "track_a_alignment.csv", index=False)
    ave = access_vs_echo(q["09_access_echo_pairs"])
    ave.to_csv(R / "access_vs_echo.csv", index=False)
    summary = dict(
        funnel=q["02_reach_funnel"].to_dict("records"),
        track_a_alignment=ta.to_dict("records"),
        access_vs_echo=ave.to_dict("records"),
        money_vs_influence_all=mvi[(mvi.user_type == "ALL")].to_dict("records"),
        coalitions=int(q["06_coalitions"].coalition.nunique()) if len(q["06_coalitions"]) else 0,
    )
    (R / "summary.json").write_text(json.dumps(summary, indent=2, default=str))
    print(f"   {len(q)} named queries -> data/results/*.csv; plus money_vs_influence, track_a_alignment, access_vs_echo")
    print(q["02_reach_funnel"].to_string(index=False))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
