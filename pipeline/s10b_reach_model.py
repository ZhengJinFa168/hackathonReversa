"""S10b Reach model: how likely is a committee amendment to survive into the EP report and to reach the final law,
and what drives it. Used by the demo to give every echoed amendment (and so every lobby comment) a probability
with an explanation.

Two L2-regularised logistic regressions on the committee amendments (labels from S04); the law model leaves out
pure deletions, which cannot reach the law under S04's definition (inserted wording kept):
    P(survived)       = P(amendment survived as an adopted report amendment)
    P(reached_law)    = P(... and its inserted wording is in Regulation 2024/1689)
Features (all known when the amendment is tabled, plus whether it echoes lobby text):
    committee, lead author's political group, tabled by a rapporteur of the lead committee, log co-signers,
    new provision / deletion, log inserted words, part of the act (recital / article / annex / Art 3 / Art 5),
    echoes lobby text (T1/T2), number of organisations echoed, user types echoed (NGO / industry).
Outputs:
    Amendment.p_survived, Amendment.p_reached_law, Amendment.p_explain (top contributions, JSON) in Neo4j;
    data/results/reach_model.csv (coefficients as odds ratios, with bootstrap 95% intervals);
    data/results/reach_model_metrics.json (5-fold cross-validated AUC and Brier score, base rates).
"""
from __future__ import annotations

import json
import logging

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, roc_auc_score
from sklearn.model_selection import StratifiedKFold, cross_val_predict

from . import config, graph

log = logging.getLogger("s10b")
SOURCE = "s10b_reach_model"
RAPPORTEURS = {"Brando BENIFEI", "Dragoş TUDORACHE"}  # IMCO-LIBE co-rapporteurs for 2021/0106(COD)
ECHO = "e.tier IN ['T1','T2'] AND NOT coalesce(e.hidden, false)"

LABELS = {
    "rapporteur": "tabled by a co-rapporteur (Benifei / Tudorache)",
    "log_cosigners": "more co-signers",
    "is_new": "adds a new provision",
    "deletion": "deletes a provision",
    "log_added_words": "longer insertion",
    "echo": "echoes lobby text",
    "log_echo_orgs": "echoes more organisations",
    "echo_ngo": "also echoes an NGO comment",
    "echo_industry": "also echoes industry comments",
}


def label(f: str) -> str:
    if f in LABELS:
        return LABELS[f]
    k, v = f.split("=", 1)
    return {"committee": f"committee {v}", "group": f"lead author in {v}", "part": f"amends {v}"}.get(k, f)


def frame() -> pd.DataFrame:
    rows = graph.run(f"""
        MATCH (a:Amendment)
        OPTIONAL MATCH (lead:MEP)-[:TABLED {{is_lead_author:true}}]->(a)
        WITH a, lead, [(m:MEP)-[t:TABLED]->(a) | t.group] AS groups, [(m:MEP)-[:TABLED]->(a) | m.name] AS names
        OPTIONAL MATCH (o:Organisation)-[:SUBMITTED]->(:Comment {{track:'B'}})<-[:PART_OF]-(:Chunk)-[e:ECHOED_IN]->(a)
        WHERE {ECHO}
        RETURN a.am_id AS am_id, a.committee AS committee, a.unit_id AS unit_id, a.target_unit_id AS target,
               a.is_new AS is_new, a.deletion AS deletion, a.n_added_words AS n_added, a.n_cosigners AS n_cosigners,
               coalesce(a.survived, false) AS survived, coalesce(a.reached_law, false) AS reached_law,
               [g IN groups WHERE g IS NOT NULL][0] AS lead_group, names,
               count(DISTINCT o) AS echo_orgs, collect(DISTINCT o.user_type) AS echo_types""")
    df = pd.DataFrame(rows)
    df["rapporteur"] = df.names.map(lambda n: int(bool(RAPPORTEURS & set(n or []))))
    t = df.target.fillna(df.unit_id).fillna("")
    df["part"] = np.select([t.str.startswith("Rec"), t.str.startswith("Annex"), t.str.match(r"^Art3\b|^Art3\("),
                            t.str.match(r"^Art5\b|^Art5\(")], ["recitals", "annexes", "Art 3 (definitions)",
                                                               "Art 5 (prohibitions)"], "other articles")
    df["part"] = np.where(t == "", "unplaced", df.part)
    return df


def design(df: pd.DataFrame) -> pd.DataFrame:
    X = pd.DataFrame(index=df.index)
    X["rapporteur"] = df.rapporteur
    X["log_cosigners"] = np.log1p(df.n_cosigners.fillna(1))
    X["is_new"] = df.is_new.astype(int)
    X["deletion"] = df.deletion.astype(int)
    X["log_added_words"] = np.log1p(df.n_added.fillna(0))
    X["echo"] = (df.echo_orgs > 0).astype(int)
    X["log_echo_orgs"] = np.log1p(df.echo_orgs)
    X["echo_ngo"] = df.echo_types.map(lambda t: int("NGO" in (t or []))).astype(int)
    X["echo_industry"] = df.echo_types.map(lambda t: int(bool({"COMPANY", "BUSINESS_ASSOCIATION"} & set(t or [])))).astype(int)
    # one-hot, dropping the most common level as the reference
    for col, ref in [("committee", "IMCO-LIBE"), ("lead_group", "S&D"), ("part", "other articles")]:
        name = "group" if col == "lead_group" else col
        for v in sorted(df[col].fillna("NA").unique()):
            if v != ref:
                X[f"{name}={v}"] = (df[col].fillna("NA") == v).astype(int)
    return X.astype(float)


def fit(X: pd.DataFrame, y: pd.Series, n_boot: int = 200) -> tuple[LogisticRegression, dict, pd.DataFrame]:
    mu, sd = X.mean(), X.std().replace(0, 1)
    Z = (X - mu) / sd
    m = LogisticRegression(C=0.5, max_iter=2000)
    cv = StratifiedKFold(5, shuffle=True, random_state=1)
    p_cv = cross_val_predict(m, Z, y, cv=cv, method="predict_proba")[:, 1]
    m.fit(Z, y)
    rng = np.random.default_rng(3)
    boots = []
    for _ in range(n_boot):
        i = rng.integers(0, len(Z), len(Z))
        if y.iloc[i].nunique() == 2:
            boots.append(LogisticRegression(C=0.5, max_iter=2000).fit(Z.iloc[i], y.iloc[i]).coef_[0])
    B = np.array(boots)
    coef = pd.DataFrame({"feature": X.columns, "coef_std": m.coef_[0],
                         "odds_ratio_per_sd": np.exp(m.coef_[0]),
                         "ci_low": np.exp(np.percentile(B, 2.5, axis=0)), "ci_high": np.exp(np.percentile(B, 97.5, axis=0)),
                         "share_with_feature": (X > 0).mean().values})
    metrics = {"auc_cv": float(roc_auc_score(y, p_cv)), "brier_cv": float(brier_score_loss(y, p_cv)),
               "base_rate": float(y.mean()), "n": int(len(y))}
    m.mu_, m.sd_ = mu, sd
    return m, metrics, coef


def explain(m: LogisticRegression, x: pd.Series, top: int = 4) -> list[dict]:
    z = (x - m.mu_) / m.sd_
    contrib = pd.Series(m.coef_[0] * z.values, index=x.index)
    contrib = contrib[(x != 0) | (x.index.str.startswith("log_"))]
    items = contrib.reindex(contrib.abs().sort_values(ascending=False).index)[:top]
    return [{"feature": label(f), "effect": "raises" if v > 0 else "lowers", "logit": round(float(v), 2)} for f, v in items.items()]


def main(track: str | None = None) -> None:
    df = frame()
    X = design(df)
    out, metrics, coefs = {}, {}, []
    for target in ("survived", "reached_law"):
        # reached_law is measured on inserted wording, so a pure deletion cannot "reach" the law by construction:
        # the law model is fitted on amendments that insert words, and deletions get no law probability
        rows = X.index if target == "survived" else X.index[~df.deletion.astype(bool)]
        Xt = X.loc[rows].drop(columns=[] if target == "survived" else ["deletion"])
        m, met, coef = fit(Xt, df.loc[rows, target].astype(int))
        df[f"p_{target}"] = np.nan
        df.loc[rows, f"p_{target}"] = m.predict_proba((Xt - m.mu_) / m.sd_)[:, 1]
        df[f"x_{target}"] = None
        df.loc[rows, f"x_{target}"] = [json.dumps(explain(m, Xt.loc[i])) for i in rows]
        metrics[target] = met
        coefs.append(coef.assign(outcome=target, label=coef.feature.map(label)))
        out[target] = m
    pd.concat(coefs).to_csv(config.RESULTS / "reach_model.csv", index=False)
    ins = ~df.deletion.astype(bool)
    metrics["echo_effect"] = {
        "survived": {"echoed": float(df.loc[df.echo_orgs > 0, "survived"].mean()),
                     "not_echoed": float(df.loc[df.echo_orgs == 0, "survived"].mean())},
        "reached_law (insertions)": {"echoed": float(df.loc[ins & (df.echo_orgs > 0), "reached_law"].mean()),
                                     "not_echoed": float(df.loc[ins & (df.echo_orgs == 0), "reached_law"].mean())}}
    (config.RESULTS / "reach_model_metrics.json").write_text(json.dumps(metrics, indent=2))
    graph.merge_nodes("Amendment", "am_id", df[["am_id", "p_survived", "p_reached_law", "x_survived", "x_reached_law"]]
                      .rename(columns={"x_survived": "p_survived_explain", "x_reached_law": "p_reached_law_explain"}), SOURCE)
    df.drop(columns=["names", "echo_types"]).to_parquet(config.CACHE / "s10b_reach_model.parquet", index=False)
    for t, met in metrics.items():
        if t != "echo_effect":
            print(f"   P({t}): cross-validated AUC {met['auc_cv']:.3f}, Brier {met['brier_cv']:.4f}, base rate {met['base_rate']:.3f}")
    print(f"   echoed vs not echoed: {json.dumps(metrics['echo_effect'])}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
