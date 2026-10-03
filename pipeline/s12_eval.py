"""S12 Evaluation.

Measures how much to trust the echoes:
1. Cross-model agreement: re-judges a stratified sample of 200 pairs per track
   with LLM_MODEL_SECOND. Reports Cohen's kappa. Disagreements downgrade the tier.
2. Null false-positive rate: judges comment ↔ DSA amendment pairs.
3. Verbatim sanity: checks if verbatim pairs are implements or partial.
4. Stance stability: how often three models agreed on the proposal's choices.

Outputs data/results/eval.csv and eval/labels.csv.
"""
from __future__ import annotations

import json
import logging
import random

import numpy as np
import pandas as pd
from sklearn.metrics import cohen_kappa_score

from . import config, graph, llm
from .s09_judge_and_stance import judge, proposal_choices, _parts, _codebook, _stance_schema, NA, STANCE_PROMPT, STANCE_SYSTEM

log = logging.getLogger("s12")

def _downgrade(tier: str) -> str:
    return {"T1": "T2", "T2": "T3", "T3": "T3"}.get(tier, tier)

def cross_model_agreement(track: str) -> tuple[float, float, int]:
    """Sample 200 pairs per track, re-judge with the second model, return kappa and agreement rate."""
    df = pd.read_parquet(config.CACHE / f"s09_judged_{track}.parquet")
    df = df[df.llm_relation.notna() & (df.hidden == False)].copy()
    if len(df) == 0:
        return 0.0, 0.0, 0
    
    # We need chunk_text and target_text (or added_text) to re-judge.
    chunks = pd.read_parquet(config.FEATURES / f"{track}_chunks.parquet").set_index("id").text
    df["chunk_text"] = df.chunk_id.map(chunks)
    
    if track == "A":
        prov = pd.read_parquet(config.CACHE / "s02_proposal_units.parquet").set_index("unit_id")
        df["target_text"] = df.target_id.map(prov.full_text)
    else:
        am = pd.read_parquet(config.CACHE / "s03_amendments.parquet").set_index("am_id")
        df["added_text"] = df.target_id.map(am.added_text)

    # Stratified sample: top-scoring (67), near threshold (67), random (66)
    n = min(200, len(df))
    n1, n2, n3 = n // 3, n // 3, n - 2 * (n // 3)
    
    df_sorted = df.sort_values("score", ascending=False)
    top = df_sorted.head(n1)
    bottom = df_sorted.tail(n2)
    remaining = df_sorted.iloc[n1:-n2]
    rand = remaining.sample(n=min(n3, len(remaining)), random_state=42)
    
    sample = pd.concat([top, bottom, rand]).drop_duplicates(subset=["chunk_id", "target_id"])
    
    log.info(f"[{track}] Cross-model agreement: re-judging {len(sample)} pairs with {config.LLM_MODEL_SECOND}")
    res = llm.batch(list(sample.itertuples()), lambda r: judge(track, r, model=config.LLM_MODEL_SECOND))
    
    y_true = sample.llm_relation.tolist()
    y_pred = [r.get("llm_relation", "unrelated") for r in res]
    
    kappa = cohen_kappa_score(y_true, y_pred)
    acc = np.mean([a == b for a, b in zip(y_true, y_pred)])
    
    # Disagreements downgrade one tier
    disagreements = []
    for (idx, r), pred in zip(sample.iterrows(), y_pred):
        if r.llm_relation != pred:
            new_tier = _downgrade(r.tier)
            if new_tier != r.tier:
                disagreements.append((r.chunk_id, r.target_id, new_tier))
    
    if disagreements:
        label, key = ("Provision", "unit_id") if track == "A" else ("Amendment", "am_id")
        q = f"""
        UNWIND $batch AS b
        MATCH (c:Chunk {{chunk_id: b.c}})-[r:ECHOED_IN]->(t:{label} {{{key}: b.t}})
        SET r.tier = b.tier
        """
        graph.run(q, batch=[{"c": c, "t": t, "tier": tier} for c, t, tier in disagreements])
        log.info(f"[{track}] Downgraded {len(disagreements)} pairs due to model disagreement.")
        
    return kappa, acc, len(disagreements)

def null_false_positive_rate() -> float:
    """Judge comment <-> DSA amendment pairs. The share it calls implements is the false-positive rate."""
    null_b = pd.read_parquet(config.CACHE / "s08_null_B.parquet")
    th = json.loads((config.DATA / "s08_thresholds.json").read_text())["B"]["tau_match"]
    null_b = null_b[null_b.score >= th]
    
    if len(null_b) == 0:
        return 0.0
        
    sample = null_b.head(min(200, len(null_b))).copy()
    chunks = pd.read_parquet(config.FEATURES / "B_chunks.parquet").set_index("id").text
    targets = pd.read_parquet(config.FEATURES / "null_dsa_targets.parquet").set_index("id").text
    
    sample["chunk_text"] = sample.chunk_id.map(chunks)
    sample["added_text"] = sample.target_id.map(targets)
    
    log.info(f"Null false-positive rate: judging {len(sample)} DSA pairs")
    res = llm.batch(list(sample.itertuples()), lambda r: judge("B", r))
    
    y_pred = [r.get("llm_relation", "unrelated") for r in res]
    fpr = np.mean([p == "implements" for p in y_pred])
    return fpr

def verbatim_sanity(track: str) -> float:
    """Every verbatim pair should come back implements or partial."""
    df = pd.read_parquet(config.CACHE / f"s09_judged_{track}.parquet")
    verb = df[df.verbatim == True]
    if len(verb) == 0:
        return 1.0
    return np.mean(verb.llm_relation.isin(["implements", "partial"]))

def stance_stability() -> tuple[float, float]:
    """How often the three models agreed on Track A proposal choices."""
    df = proposal_choices()
    unanimous = (df.agreement == 1.0).sum()
    total = len(df)
    return unanimous / total if total else 0.0, df.agreement.mean()

def generate_labels_csv():
    """Optional eval/labels.csv where a person labels 30 pairs."""
    try:
        a = pd.read_parquet(config.CACHE / "s09_judged_A.parquet")
        b = pd.read_parquet(config.CACHE / "s09_judged_B.parquet")
        sample = pd.concat([a.head(15), b.head(15)])
        
        chunks = pd.read_parquet(config.FEATURES / "B_chunks.parquet").set_index("id").text
        chunks_a = pd.read_parquet(config.FEATURES / "A_chunks.parquet").set_index("id").text
        
        out = []
        for r in sample.itertuples():
            ctext = chunks_a.get(r.chunk_id) if r.track == "A" else chunks.get(r.chunk_id)
            out.append({
                "track": r.track,
                "chunk_id": r.chunk_id,
                "target_id": r.target_id,
                "score": r.score,
                "llm_relation": r.llm_relation,
                "human_relation": "",
                "chunk_text": ctext
            })
        
        config.EVAL.mkdir(exist_ok=True)
        pd.DataFrame(out).to_csv(config.EVAL / "labels.csv", index=False)
    except Exception as e:
        log.warning(f"Could not generate labels.csv: {e}")

def main():
    log.info("Starting S12 Evaluation...")
    out = {}
    
    # 1. Cross-model agreement
    for t in ["A", "B"]:
        kappa, acc, downgrades = cross_model_agreement(t)
        out[f"cross_model_kappa_{t}"] = kappa
        out[f"cross_model_acc_{t}"] = acc
        out[f"cross_model_downgrades_{t}"] = downgrades
        
    # 2. Null false-positive rate
    fpr = null_false_positive_rate()
    out["null_false_positive_rate"] = fpr
    
    # 3. Verbatim sanity
    out["verbatim_sanity_A"] = verbatim_sanity("A")
    out["verbatim_sanity_B"] = verbatim_sanity("B")
    
    # 4. Stance stability
    unanimous, mean_agree = stance_stability()
    out["stance_stability_unanimous"] = unanimous
    out["stance_stability_mean"] = mean_agree
    
    # Save results
    df = pd.DataFrame([out])
    df.to_csv(config.RESULTS / "eval.csv", index=False)
    log.info(f"Saved evaluation metrics to {config.RESULTS / 'eval.csv'}")
    
    # Optional labels
    generate_labels_csv()
    log.info("Finished S12.")

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s [%(name)s] %(message)s")
    main()
