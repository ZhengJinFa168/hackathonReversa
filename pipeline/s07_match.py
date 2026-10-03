"""S07 Matching: candidate (chunk, target) pairs and their scores; SIMILAR_TO between near-duplicate chunks.

Targets: Track A -> Provision (proposal text), Track B -> Amendment.added_text (S05 features).
Candidates per chunk:
  - top-10 targets by embedding cosine;
  - every target sharing a 6-gram that is not in the proposal stoplist (Track B; Track A has no stoplist);
  - Track B: amendments on articles the chunk cites, the 20 closest by embedding per chunk
    (all of them would be hundreds per chunk for much-amended articles such as Art 10).
Scores: s_embed (cosine), s_tfidf (cosine of 1-2-gram TF-IDF), s_ngram (share of the target's word
3-grams, outside stoplisted spans, that occur in the chunk; denominator + 5), longest_run (longest identical word run outside
stoplisted spans), verbatim = longest_run >= 12.
    score = 0.5 z(s_embed) + 0.3 z(s_tfidf) + 0.2 z(s_ngram)
z uses the mean and standard deviation over *random* chunk-target pairs of the track (not the candidates), so a
score says how far a pair is above chance; the stats are saved for S08's null distribution.
Common quotes: 12-grams present in the comments of >= 8 organisations (document titles, widely quoted
definitions) are masked like the stoplist, so they create neither verbatim matches nor SIMILAR_TO edges.
Near-duplicates (SIMILAR_TO): chunk pairs from different organisations sharing an identical run of >= 12 words
outside the stoplist, or with cosine >= 0.97 and TF-IDF cosine >= 0.5 (a shared position paper or template).
The plan's 0.9 cosine cut-off is not used: with e5 most AI-policy paragraphs are above 0.9.
"""
from __future__ import annotations

import json
import logging
import pickle
from difflib import SequenceMatcher

import numpy as np
import pandas as pd
import scipy.sparse as sp

from . import config, graph
from .s05_features import N, ngram_tokens, ngrams, stoplist, tokens
from .units import top

log = logging.getLogger("s07")
SOURCE = "s07_match"
F = config.FEATURES
RNG = np.random.default_rng(7)
CITED_PER_CHUNK = 20
NGRAM_SMOOTH = 5         # added to the 3-gram denominator
NGRAM_STD_FLOOR = 0.05   # random pairs share almost no 3-grams; without a floor z(s_ngram) explodes
DUP_RUN, DUP_COS, DUP_TFIDF = 12, 0.97, 0.5


def load(track: str, kind: str):
    df = pd.read_parquet(F / f"{track}_{kind}.parquet")
    return df, np.load(F / f"{track}_{kind}_emb.npy"), sp.load_npz(F / f"{track}_{kind}_tfidf.npz")


class _Gap:  # a masked (stoplisted) position: never equal to anything, so runs cannot cross it
    def __eq__(self, other):
        return False

    def __hash__(self):
        return id(self)


COMMON_N, COMMON_ORGS = 12, 8


def common_quotes() -> set[tuple]:
    """12-grams that appear in the comments of >= 8 organisations: document titles ("White Paper on Artificial
    Intelligence - A European approach to excellence and trust"), the inception impact assessment's title, and
    widely quoted definitions. They are citations, not a position, so they are masked like the stoplist."""
    org = dict(graph.run("MATCH (o:Organisation)-[:SUBMITTED]->(:Comment)<-[:PART_OF]-(c:Chunk) "
                         "RETURN collect([c.chunk_id, o.org_id]) AS m")[0]["m"])
    seen: dict[tuple, set] = {}
    for t in "AB":
        C = pd.read_parquet(F / f"{t}_chunks.parquet")
        for cid, text in zip(C.id, C.text):
            for g in set(ngrams(tokens(text), COMMON_N)):
                seen.setdefault(g, set()).add(org.get(cid))
    return {g for g, o in seen.items() if len(o) >= COMMON_ORGS}


def masked(text, stop, common: set[tuple] = frozenset()) -> list:
    toks = ngram_tokens(text, stop)
    if common:
        raw = tokens(text)
        for i, g in enumerate(ngrams(raw, COMMON_N)):
            if g in common:
                for k in range(i, i + COMMON_N):
                    toks[k] = None
    return [t if t is not None else _Gap() for t in toks]


def longest_run(a: list, b: list) -> tuple[int, str]:
    if not a or not b:
        return 0, ""
    m = SequenceMatcher(None, a, b, autojunk=False).find_longest_match(0, len(a), 0, len(b))
    return m.size, " ".join(str(x) for x in a[m.a : m.a + m.size])


def tri(tokens: list) -> set:
    return {g for g in ngrams([t if isinstance(t, str) else None for t in tokens], 3) if None not in g}


def pair_features(chunk_tok: list, target_tok: list, target_tri: set) -> dict:
    run, run_text = longest_run(chunk_tok, target_tok)
    # smoothed share: a target with 2 free 3-grams that both occur is not "100% shared"
    s_ngram = len(target_tri & tri(chunk_tok)) / (len(target_tri) + NGRAM_SMOOTH)
    return dict(s_ngram=s_ngram, longest_run=run, run_text=run_text if run >= 5 else None)


def random_stats(Ce, Ge, Cx, Gx, c_tok, g_tok, g_tri, n: int = 20000) -> dict:
    i = RNG.integers(0, len(Ce), n)
    j = RNG.integers(0, len(Ge), n)
    e = np.einsum("ij,ij->i", Ce[i], Ge[j])
    t = np.asarray(Cx[i].multiply(Gx[j]).sum(axis=1)).ravel()
    g = np.array([len(g_tri[b] & tri(c_tok[a])) / (len(g_tri[b]) + NGRAM_SMOOTH) for a, b in zip(i[:5000], j[:5000])])
    floor = 1e-3
    return dict(embed_mean=float(e.mean()), embed_std=max(float(e.std()), floor),
                tfidf_mean=float(t.mean()), tfidf_std=max(float(t.std()), floor),
                ngram_mean=float(g.mean()), ngram_std=max(float(g.std()), NGRAM_STD_FLOOR))


def combine(df: pd.DataFrame, st: dict) -> pd.Series:
    w = config.SCORE_WEIGHTS
    return (w["s_embed"] * (df.s_embed - st["embed_mean"]) / st["embed_std"]
            + w["s_tfidf"] * (df.s_tfidf - st["tfidf_mean"]) / st["tfidf_std"]
            + w["s_ngram"] * (df.s_ngram - st["ngram_mean"]) / st["ngram_std"])


def match_track(track: str, common: set[tuple]) -> tuple[pd.DataFrame, dict]:
    C, Ce, Cx = load(track, "chunks")
    G, Ge, Gx = load(track, "targets")
    stop = stoplist(track)
    c_tok = [masked(t, stop, common) for t in C.text]
    g_tok = [masked(t, stop, common) for t in G.text]
    g_tri = [tri(t) for t in g_tok]

    # n-gram index of targets (6-grams outside stoplisted spans)
    inv: dict[tuple, set[int]] = {}
    for j, toks in enumerate(g_tok):
        for g in ngrams([t if isinstance(t, str) else None for t in toks], N):
            if None not in g:
                inv.setdefault(g, set()).add(j)

    S = Ce @ Ge.T
    cand: dict[tuple[int, int], set[str]] = {}
    for i in range(len(C)):
        for j in np.argpartition(-S[i], config.TOPK_EMBED)[: config.TOPK_EMBED]:
            cand.setdefault((i, int(j)), set()).add("embed_top10")
        for g in ngrams([t if isinstance(t, str) else None for t in c_tok[i]], N):
            if None not in g:
                for j in inv.get(g, ()):
                    cand.setdefault((i, j), set()).add("shared_6gram")
    if track == "B":
        cited = pd.read_parquet(config.CACHE / "s01_chunks_B.parquet").set_index("chunk_id").cited_articles
        by_art: dict[str, list[int]] = {}
        for j, t in enumerate(G.target_unit_id):
            if isinstance(t, str):
                by_art.setdefault(top(t), []).append(j)
        for i, cid in enumerate(C.id):
            js = [j for a in (cited.get(cid) if cited.get(cid) is not None else []) for j in by_art.get(a, [])]
            if js:
                js = np.array(js)
                for j in js[np.argsort(-S[i, js])[:CITED_PER_CHUNK]]:
                    cand.setdefault((i, int(j)), set()).add("cited_article")
    log.info("track %s: %d candidate pairs", track, len(cand))

    rows = []
    keys = list(cand)
    ii = np.array([k[0] for k in keys])
    jj = np.array([k[1] for k in keys])
    s_tfidf = np.asarray(Cx[ii].multiply(Gx[jj]).sum(axis=1)).ravel()
    for (i, j), tf in zip(keys, s_tfidf):
        f = pair_features(c_tok[i], g_tok[j], g_tri[j])
        rows.append(dict(chunk_id=C.id.iat[i], target_id=G.id.iat[j], track=track, s_embed=float(S[i, j]),
                         s_tfidf=float(tf), **f, candidate_sources=",".join(sorted(cand[(i, j)]))))
    df = pd.DataFrame(rows)
    df["verbatim"] = df.longest_run >= config.VERBATIM_RUN
    st = random_stats(Ce, Ge, Cx, Gx, c_tok, g_tok, g_tri)
    df["score"] = combine(df, st)
    return df, st


# ---------------------------------------------------------------- near-duplicate chunks
def similar_chunks(common: set[tuple]) -> pd.DataFrame:
    parts = [load(t, "chunks") for t in "AB"]
    C = pd.concat([p[0] for p in parts], ignore_index=True)
    E = np.vstack([p[1] for p in parts])
    X = sp.vstack([p[2] for p in parts]).tocsr()
    org = dict(graph.run("MATCH (o:Organisation)-[:SUBMITTED]->(:Comment)<-[:PART_OF]-(c:Chunk) "
                         "RETURN collect([c.chunk_id, o.org_id]) AS m")[0]["m"])
    C["org"] = C.id.map(org)
    stop = stoplist("B")
    tok = [masked(t, stop, common) for t in C.text]
    # candidate pairs: high cosine, or a shared 12-gram
    inv: dict[tuple, set[int]] = {}
    for i, t in enumerate(tok):
        for g in ngrams([x if isinstance(x, str) else None for x in t], DUP_RUN):
            if None not in g:
                inv.setdefault(g, set()).add(i)
    pairs = {tuple(sorted((a, b))) for s in inv.values() if 1 < len(s) < 50 for a in s for b in s if a != b}
    for i in range(0, len(E), 1000):
        S = E[i : i + 1000] @ E.T
        for a, b in zip(*np.nonzero(S >= DUP_COS)):
            if i + a < b:
                pairs.add((i + a, int(b)))
    rows = []
    for a, b in pairs:
        if C.org.iat[a] == C.org.iat[b]:
            continue
        cos = float(E[a] @ E[b])
        tf = float(X[a].multiply(X[b]).sum())
        run, run_text = longest_run(tok[a], tok[b])
        if run >= DUP_RUN or (cos >= DUP_COS and tf >= DUP_TFIDF):
            rows.append(dict(start=C.id.iat[a], end=C.id.iat[b], score=cos, s_tfidf=tf, longest_run=run,
                             run_text=run_text if run >= DUP_RUN else None))
    return pd.DataFrame(rows)


def main(track: str | None = None) -> None:
    stats = {}
    common = common_quotes()
    pickle.dump(common, open(F / "common_quotes_12grams.pkl", "wb"))
    print(f"   common quotes masked: {len(common)} 12-grams found in >= {COMMON_ORGS} organisations' comments")
    for t in ([track] if track else ["A", "B"]):
        df, st = match_track(t, common)
        df.to_parquet(config.CACHE / f"s07_candidates_{t}.parquet", index=False)
        stats[t] = st
        print(f"   track {t}: {len(df):,} candidate pairs for {df.chunk_id.nunique():,} chunks; "
              f"{df.verbatim.sum()} verbatim (run >= {config.VERBATIM_RUN}); sources "
              f"{df.candidate_sources.str.split(',').explode().value_counts().to_dict()}")
    p = config.FEATURES / "s07_random_stats.json"
    old = json.loads(p.read_text()) if p.exists() else {}
    p.write_text(json.dumps({**old, **stats}, indent=2))
    dup = similar_chunks(common)
    dup.to_parquet(config.CACHE / "s07_similar_to.parquet", index=False)
    graph.run("MATCH ()-[r:SIMILAR_TO]->() DELETE r")
    graph.merge_rels("SIMILAR_TO", "Chunk", "chunk_id", "Chunk", "chunk_id", dup, SOURCE)
    print(f"   SIMILAR_TO: {len(dup)} near-duplicate chunk pairs across organisations")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
