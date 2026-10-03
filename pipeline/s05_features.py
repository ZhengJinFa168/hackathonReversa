"""S05 Features for matching: proposal 6-gram stoplist, TF-IDF, embeddings.

Sources (parquet caches written by S01-S03, the same rows that are in Neo4j):
  chunks   : s01_chunks_{A,B}            (lobby text, ~80-150 words)
  targets A: s02_proposal_units          (Commission proposal units with >= 5 words of their own text)
  targets B: s03_amendments.added_text   (only the words the committee amendment inserts)

- Stoplist: every word 6-gram of the proposal. Comments and amendments both quote the proposal, so shared
  6-grams that come from it are not evidence of an echo. `ngram_tokens()` returns tokens with stoplisted
  positions masked, for S07's n-gram overlap and longest-run.
  The stoplist applies to Track B only: in Track A the proposal *is* the target, and the comments predate
  it (0.2% of their words fall in proposal 6-grams), so a shared 6-gram there is the echo itself.
  `stoplist(track)` returns the right set.
- TF-IDF: word 1-2-grams, sublinear tf, fitted on chunks + targets of both tracks.
- Embeddings: intfloat/multilingual-e5-base via pipeline/embed.py (cached by text hash); chunk vectors are also
  written to Chunk.embedding for the Neo4j vector index.

Outputs in data/features/: stoplist_6grams.pkl, tfidf.pkl, {A,B}_{chunks,targets}.parquet (ids + text),
{A,B}_{chunks,targets}_emb.npy, {A,B}_{chunks,targets}_tfidf.npz
"""
from __future__ import annotations

import logging
import pickle
import re

import numpy as np
import pandas as pd
import scipy.sparse as sp
from sklearn.feature_extraction.text import TfidfVectorizer

from . import config, graph
from .embed import embed
from .textutil import ascii_fold

log = logging.getLogger("s05")
SOURCE = "s05_features"
F = config.FEATURES
N = config.STOPLIST_N
MIN_TARGET_WORDS = 5


def tokens(s) -> list[str]:
    s = ascii_fold(s if isinstance(s, str) else "").lower()
    return re.findall(r"[a-z0-9]+", s)


def ngrams(toks: list[str], n: int = N) -> list[tuple]:
    return [tuple(toks[i : i + n]) for i in range(len(toks) - n + 1)]


def build_stoplist() -> set[tuple]:
    prov = pd.read_parquet(config.CACHE / "s02_proposal_units.parquet")
    stop: set[tuple] = set()
    for t in prov.text.fillna("").tolist() + prov.title.fillna("").tolist():
        stop.update(ngrams(tokens(t)))
    return stop


def stoplist(track: str) -> set[tuple]:
    if track == "A":
        return set()
    return pickle.load(open(F / "stoplist_6grams.pkl", "rb"))


def ngram_tokens(text, stop: set[tuple]) -> list[str | None]:
    """Tokens with every position covered by a stoplisted 6-gram replaced by None."""
    toks = tokens(text)
    masked = list(toks)
    for i, g in enumerate(ngrams(toks)):
        if g in stop:
            for k in range(i, i + N):
                masked[k] = None
    return masked


def stoplisted_share(text, stop) -> float:
    m = ngram_tokens(text, stop)
    return sum(t is None for t in m) / len(m) if m else 0.0


def _prep(s: str) -> str:
    return ascii_fold(s).lower()


def chunks(track: str) -> pd.DataFrame:
    c = pd.read_parquet(config.CACHE / f"s01_chunks_{track}.parquet")
    return c[["chunk_id", "feedback_id", "text", "n_words"]].rename(columns={"chunk_id": "id"})


def targets(track: str) -> pd.DataFrame:
    if track == "A":
        p = pd.read_parquet(config.CACHE / "s02_proposal_units.parquet")
        p = p[p.text.fillna("").str.split().str.len() >= MIN_TARGET_WORDS]
        return p[["unit_id", "kind", "article", "text"]].rename(columns={"unit_id": "id"}).reset_index(drop=True)
    a = pd.read_parquet(config.CACHE / "s03_amendments.parquet")
    a = a[a.added_text.notna() & (a.n_added_words > 0)]
    return a[["am_id", "target_unit_id", "location", "added_text"]].rename(
        columns={"am_id": "id", "added_text": "text"}).reset_index(drop=True)


def main(track: str | None = None) -> None:
    stop = build_stoplist()
    pickle.dump(stop, open(F / "stoplist_6grams.pkl", "wb"))
    tracks = ["A", "B"]  # TF-IDF is fitted on both tracks together, so features are always built for both
    tabs = {(t, k): (chunks(t) if k == "chunks" else targets(t)) for t in tracks for k in ("chunks", "targets")}

    vec = TfidfVectorizer(ngram_range=(1, 2), sublinear_tf=True, min_df=2, max_df=0.5,
                          token_pattern=r"(?u)\b[a-z0-9]{2,}\b", preprocessor=_prep)
    vec.fit(pd.concat([df.text for df in tabs.values()]).fillna("").tolist())
    pickle.dump(vec, open(F / "tfidf.pkl", "wb"))

    for (t, k), df in tabs.items():
        df = df.assign(stoplisted_share=[stoplisted_share(x, stop) for x in df.text])
        df.to_parquet(F / f"{t}_{k}.parquet", index=False)
        sp.save_npz(F / f"{t}_{k}_tfidf.npz", vec.transform(df.text.fillna("").tolist()).tocsr())
        np.save(F / f"{t}_{k}_emb.npy", embed(df.text.fillna("").tolist()))
        log.info("%s %s: %d rows", t, k, len(df))

    # vectors into Neo4j for the vector indexes (exploration and the demo): chunks, proposal units (Track A
    # targets) and amendments (Track B targets, embedding of the inserted words)
    graph.apply_schema()
    for t, kind, label, key in [("A", "chunks", "Chunk", "chunk_id"), ("B", "chunks", "Chunk", "chunk_id"),
                                ("A", "targets", "Provision", "unit_id"), ("B", "targets", "Amendment", "am_id")]:
        ids = pd.read_parquet(F / f"{t}_{kind}.parquet").id.tolist()
        emb = np.load(F / f"{t}_{kind}_emb.npy")
        rows = [dict(id=i, embedding=v.tolist()) for i, v in zip(ids, emb)]
        for b in range(0, len(rows), 500):
            graph.run(f"UNWIND $rows AS r MATCH (n:{label} {{{key}: r.id}}) "
                      "CALL db.create.setNodeVectorProperty(n, 'embedding', r.embedding)", rows=rows[b : b + 500])

    print(f"   stoplist: {len(stop):,} proposal 6-grams; TF-IDF vocabulary: {len(vec.vocabulary_):,} terms")
    for (t, k) in tabs:
        df = pd.read_parquet(F / f"{t}_{k}.parquet")
        print(f"   {t} {k}: {len(df):,} rows, mean stoplisted share {df.stoplisted_share.mean():.1%}, "
              f"rows >50% stoplisted {(df.stoplisted_share > .5).mean():.1%}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
