"""S04 Survival -> AdoptedAmendment, SURVIVED_AS, BECAME; sets Amendment.survived / reached_law.

1. AdoptedAmendment: the 771 report amendments A9-0188/2023-1..771 (IMCO-LIBE report, plenary June 2023).
   The plenary dump also holds 37 amendments tabled in plenary by groups (A9-0188/2023-774..807); they are
   not report amendments and are left out.
2. Committee amendment -> adopted amendment on the same proposal unit, scored on what the committee amendment
   *inserts* (content words not already in the proposal unit), so shared proposal wording doesn't count:
       survival_score = 0.5 * share of inserted content words found in the adopted text
                      + 0.3 * share of inserted bigrams found in it + 0.2 * cosine(e5(inserted), e5(adopted inserted))
   Deletions survive when the adopted amendment on that unit also deletes it (score 1).
   tau_s = 99th percentile of a null: the same committee amendments scored against as many adopted amendments
   on *other* articles, best of those. SURVIVED_AS = best adopted match with score >= tau_s
   (compromises merge several committee amendments into one adopted one, so several committee amendments
   may survive as the same adopted amendment; `mutual_best` marks the strict one-to-one pairs).
3. BECAME (Provision | AdoptedAmendment -> LegalUnit): the final act was renumbered, so units are matched by
   text: top-10 by embedding, scored 0.5 * cosine + 0.5 * token_set_ratio, best one kept when >= tau_b
   (99th percentile of the best score over 10 random final units of the same kind). Final units are compared
   in their 2024 as-adopted wording.
4. reached_law: for adopted amendments, BECAME plus `added_kept` = share of the amendment's inserted content
   words (not already in the proposal) found in the law unit, >= tau_k (99th percentile against random law units). A committee amendment
   reached the law when it survived as an adopted amendment that reached the law.
"""
from __future__ import annotations

import json
import logging
import re

import numpy as np
import pandas as pd
from rapidfuzz import fuzz

from . import config, graph, sources
from .embed import embed
from .units import location_to_unit, resolve, top

log = logging.getLogger("s04")
SOURCE = "s04_survival"
RNG = np.random.default_rng(42)
REPORT_RE = re.compile(r"^A9-0188/2023-(\d+)$")
N_REPORT = 771

STOP = set("""a an the and or of to in on for by with as at from that this these those be is are was were been being
it its their they them which who whom whose such any all each other than then also not no nor but if where when
shall may should must can could would will within under into upon between including referred paragraph article
point annex regulation union member states state""".split())


def words(s) -> set[str]:
    s = s if isinstance(s, str) else ""
    return {w for w in re.findall(r"[a-zà-ÿ0-9]+", s.lower()) if len(w) > 2 and w not in STOP}


def recall(a: str, b: str) -> float | None:
    wa = words(a)
    return len(wa & words(b)) / len(wa) if wa else None


def tset(a, b) -> float:
    return fuzz.token_set_ratio(a if isinstance(a, str) else "", b if isinstance(b, str) else "") / 100.0


# ---------------------------------------------------------------- 1. adopted amendments
def load_adopted() -> pd.DataFrame:
    import importlib.util

    spec = importlib.util.spec_from_file_location("bd", config.ROOT / "amendments" / "build_dashboard.py")
    bd = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bd)
    provisions = set(pd.read_parquet(config.CACHE / "s02_proposal_units.parquet").unit_id)
    rows, skipped = [], 0
    for r in sources.iter_zst_json(sources.path("parltrack_plenary_amendments"), json.dumps(config.PROCEDURE)):
        m = REPORT_RE.match(str(r.get("id", "")))
        if r.get("reference") != config.PROCEDURE or not m or int(m.group(1)) > N_REPORT:
            skipped += 1
            continue
        loc = bd.clean((r.get("location") or [[None, ""]])[0][-1])
        old, new = bd.join_lines(r.get("old")), bd.join_lines(r.get("new"))
        if new.lower().rstrip(".") == "deleted":
            new = ""
        _, _, added = bd.word_diff(old, new)
        u = location_to_unit(loc)
        target, exact = resolve(u["base_unit_id"] if u["is_new"] else u["unit_id"], provisions) if u else (None, False)
        is_new = bool(u and u["is_new"])
        rows.append(dict(adopted_id=r["id"], seq=int(m.group(1)), date=r["date"][:10], location=loc,
                         unit_id=u["unit_id"] if u else None, target_unit_id=target, is_new=is_new,
                         old_text=old or None, text=new or None,
                         added_text=(new if is_new and not old else added) or None,
                         deletion=bool(old) and not new))
    log.info("%d report amendments kept, %d other plenary amendments skipped", len(rows), skipped)
    return pd.DataFrame(rows)


# ---------------------------------------------------------------- 2. survival
def bigrams(s) -> set[tuple[str, str]]:
    toks = re.findall(r"[a-zà-ÿ0-9]+", s.lower()) if isinstance(s, str) else []
    return {(a, b) for a, b in zip(toks, toks[1:]) if not (a in STOP and b in STOP)}


def novel_words(added, old) -> set[str]:
    """Content words the amendment inserts that were not already in the proposal text of the unit."""
    return words(added) - words(old)


def _surv_score(c, a, ce, ae) -> float | None:
    """Did the committee amendment's *inserted* wording make it into the adopted amendment?
    0.5 * share of its new content words in the adopted text + 0.3 * share of its inserted bigrams
    + 0.2 * cosine(inserted text, adopted inserted text). Scoring full texts instead would mostly measure
    the proposal wording both amendments share."""
    if c.deletion:
        return 1.0 if a.deletion else 0.0
    if a.deletion or not isinstance(a.text, str):
        return 0.0
    nw = novel_words(c.added_text, c.old_text)
    if not nw:
        return None  # only reorders or function words: not measurable
    bg = bigrams(c.added_text)
    kw = len(nw & words(a.text)) / len(nw)
    kb = len(bg & bigrams(a.text)) / len(bg) if bg else kw
    return 0.5 * kw + 0.3 * kb + 0.2 * float(ce @ ae)


def survival(com: pd.DataFrame, ad: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, float]:
    ce = embed(com.added_text.fillna(com.old_text).fillna("").tolist())
    ae = embed(ad.added_text.fillna(ad.text).fillna(ad.old_text).fillna("").tolist())
    by_unit: dict[str, list[int]] = {}
    for j, t in enumerate(ad.target_unit_id):
        if isinstance(t, str):
            by_unit.setdefault(t, []).append(j)
    ad_top = np.array([top(t) if isinstance(t, str) else "" for t in ad.target_unit_id])
    pairs, null = [], []
    for i, c in enumerate(com.itertuples()):
        cands = by_unit.get(c.target_unit_id, []) if isinstance(c.target_unit_id, str) else []
        scores = [(_surv_score(c, ad.iloc[j], ce[i], ae[j]), j) for j in cands]
        scores = [(s, j) for s, j in scores if s is not None]
        for s, j in scores:
            pairs.append(dict(am_id=c.am_id, adopted_id=ad.adopted_id.iat[j], score=s))
        if scores and not c.deletion:  # null: as many adopted amendments from other articles, best score
            pool = np.flatnonzero(ad_top != top(c.target_unit_id))
            pick = RNG.choice(pool, size=min(len(scores), len(pool)), replace=False)
            null.append(max(_surv_score(c, ad.iloc[j], ce[i], ae[j]) or 0.0 for j in pick))
    p = pd.DataFrame(pairs)
    tau = float(np.percentile(null, config.NULL_PERCENTILE))
    best = p.sort_values("score", ascending=False).drop_duplicates("am_id")
    best_rev = p.sort_values("score", ascending=False).drop_duplicates("adopted_id")
    rev = set(zip(best_rev.am_id, best_rev.adopted_id))
    best["mutual_best"] = [(a, b) in rev for a, b in zip(best.am_id, best.adopted_id)]
    edges = best[best.score >= tau]
    log.info("survival: %d pairs, tau_s=%.3f, %d committee amendments survive", len(p), tau, len(edges))
    return best, edges, tau


# ---------------------------------------------------------------- 3. BECAME
def law_units() -> pd.DataFrame:
    f = pd.read_parquet(config.CACHE / "s02_final_units.parquet")
    f = f.assign(cmp_text=f.text_as_adopted.fillna(f.text))
    f = f[f.cmp_text.str.split().str.len() >= 3].reset_index(drop=True)
    f["group"] = np.where(f.kind == "recital", "recital", "body")
    return f


def match_to_law(src: pd.DataFrame, key: str, text_col: str, group_col: str, law: pd.DataFrame, le: np.ndarray):
    """Best law unit per source row (top-10 by embedding, then combined score), plus a random-unit null."""
    src = src[src[text_col].fillna("").str.split().str.len() >= 3]
    se = embed(src[text_col].tolist())
    rows, null = [], []
    for g in ("recital", "body"):
        m_src = (src[group_col] == g).to_numpy()
        idx_law = np.flatnonzero(law.group.to_numpy() == g)
        if not m_src.any() or not len(idx_law):
            continue
        sims = se[m_src] @ le[idx_law].T
        for row, s_row, e_row in zip(src[m_src].itertuples(), sims, se[m_src]):
            text = getattr(row, text_col)
            top10 = idx_law[np.argsort(-s_row)[:10]]
            scored = [(0.5 * float(e_row @ le[j]) + 0.5 * tset(text, law.cmp_text.iat[j]), j) for j in top10]
            sc, j = max(scored)
            rows.append({key: getattr(row, key), "law_unit_id": law.law_unit_id.iat[j], "score": sc,
                         "law_text": law.cmp_text.iat[j]})
            rnd = RNG.choice(idx_law, size=min(10, len(idx_law)), replace=False)
            null.append(max(0.5 * float(e_row @ le[k]) + 0.5 * tset(text, law.cmp_text.iat[k]) for k in rnd))
    return pd.DataFrame(rows), float(np.percentile(null, config.NULL_PERCENTILE))


def became(ad: pd.DataFrame) -> dict:
    law = law_units()
    le = embed(law.cmp_text.tolist())
    prov = pd.read_parquet(config.CACHE / "s02_proposal_units.parquet")
    prov = prov.assign(group=np.where(prov.kind == "recital", "recital", "body"))
    p_best, tau_p = match_to_law(prov, "unit_id", "text", "group", law, le)
    ad2 = ad.assign(group=np.where(ad.unit_id.fillna("").str.startswith("Rec"), "recital", "body"))
    a_best, tau_a = match_to_law(ad2[~ad2.deletion], "adopted_id", "text", "group", law, le)
    # added_kept: share of the adopted amendment's inserted content words present in the law unit
    a = ad.set_index("adopted_id")
    nov = {k: novel_words(x, o) for k, x, o in zip(a.index, a.added_text, a.old_text)}

    def kept(k, law_text):
        return len(nov[k] & words(law_text)) / len(nov[k]) if nov.get(k) else None

    a_best["added_kept"] = [kept(k, t) for k, t in zip(a_best.adopted_id, a_best.law_text)]
    rnd = law.cmp_text.sample(len(a_best), replace=True, random_state=1).tolist()
    null_k = [kept(k, t) for k, t in zip(a_best.adopted_id, rnd)]
    tau_k = float(np.nanpercentile([x for x in null_k if x is not None], config.NULL_PERCENTILE))
    log.info("BECAME: tau provision=%.3f, tau adopted=%.3f, tau added_kept=%.3f", tau_p, tau_a, tau_k)
    return dict(prov=p_best, adopted=a_best, tau_p=tau_p, tau_a=tau_a, tau_k=tau_k)


# ---------------------------------------------------------------- run
def main(track: str | None = None) -> None:
    ad = load_adopted()
    com = pd.read_parquet(config.CACHE / "s03_amendments.parquet")
    best, surv, tau_s = survival(com, ad)
    b = became(ad)
    a_b = b["adopted"]
    a_b["became"] = a_b.score >= b["tau_a"]
    a_b["reached_law"] = a_b.became & (a_b.added_kept.fillna(0) >= b["tau_k"])
    reached_ad = set(a_b.loc[a_b.reached_law, "adopted_id"])

    ad = ad.merge(a_b[["adopted_id", "law_unit_id", "score", "added_kept", "reached_law"]]
                  .rename(columns={"score": "law_score"}), on="adopted_id", how="left")
    ad["reached_law"] = ad.reached_law.fillna(False).astype(bool)
    com_props = best.rename(columns={"score": "survival_score"})[["am_id", "survival_score"]]
    com_props = com[["am_id"]].merge(com_props, on="am_id", how="left")
    sv = surv.set_index("am_id").adopted_id
    com_props["survived"] = com_props.am_id.isin(sv.index)
    com_props["survived_as"] = com_props.am_id.map(sv)
    com_props["reached_law"] = com_props.survived_as.isin(reached_ad)

    graph.merge_nodes("AdoptedAmendment", "adopted_id", ad.drop(columns=["law_unit_id"]), SOURCE)
    graph.merge_nodes("Amendment", "am_id", com_props.drop(columns=["survived_as"]), SOURCE)
    graph.merge_rels("SURVIVED_AS", "Amendment", "am_id", "AdoptedAmendment", "adopted_id",
                     surv.rename(columns={"am_id": "start", "adopted_id": "end"})[["start", "end", "score", "mutual_best"]], SOURCE)
    pb = b["prov"][b["prov"].score >= b["tau_p"]]
    graph.merge_rels("BECAME", "Provision", "unit_id", "LegalUnit", "law_unit_id",
                     pb.rename(columns={"unit_id": "start", "law_unit_id": "end"})[["start", "end", "score"]], SOURCE)
    ab = a_b[a_b.became]
    graph.merge_rels("BECAME", "AdoptedAmendment", "adopted_id", "LegalUnit", "law_unit_id",
                     ab.rename(columns={"adopted_id": "start", "law_unit_id": "end"})[["start", "end", "score", "added_kept"]], SOURCE)

    thresholds = dict(tau_s=tau_s, tau_became_provision=b["tau_p"], tau_became_adopted=b["tau_a"], tau_added_kept=b["tau_k"])
    (config.DATA / "s04_thresholds.json").write_text(json.dumps(thresholds, indent=2))
    for name, df in dict(adopted=ad, survival_best=best, became_prov=b["prov"], became_adopted=a_b, amendment_props=com_props).items():
        df.to_parquet(config.CACHE / f"s04_{name}.parquet", index=False)
    print(f"   {len(ad)} adopted amendments; thresholds {json.dumps({k: round(v, 3) for k, v in thresholds.items()})}")
    print(f"   committee amendments: {com_props.survival_score.notna().sum()} with a candidate on the same unit, "
          f"{com_props.survived.sum()} survived ({com_props.survived.mean():.1%}), {com_props.reached_law.sum()} reached the law "
          f"({com_props.reached_law.mean():.1%})")
    print(f"   BECAME: {len(pb)}/{len(b['prov'])} provisions, {len(ab)}/{len(a_b)} adopted amendments; "
          f"adopted amendments that reached the law: {ad.reached_law.sum()}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
