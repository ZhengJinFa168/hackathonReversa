"""Local embeddings (intfloat/multilingual-e5-base), cached by text hash in data/features/embeddings.sqlite.

embed(texts) -> float32 array (n, 768), L2-normalised, so cosine = dot product.
e5 expects a prefix; every text is embedded as a "passage: " (symmetric similarity).
"""
from __future__ import annotations

import hashlib
import sqlite3

import numpy as np

from . import config

_model = None
DB = config.FEATURES / "embeddings.sqlite"


def _key(text: str) -> str:
    return hashlib.sha1((config.EMBED_MODEL + "\x00" + text).encode()).hexdigest()


def model():
    global _model
    if _model is None:
        import torch
        from sentence_transformers import SentenceTransformer

        device = "mps" if torch.backends.mps.is_available() else "cpu"
        _model = SentenceTransformer(config.EMBED_MODEL, device=device)
    return _model


def embed(texts: list[str], batch_size: int = 32) -> np.ndarray:
    texts = [t or "" for t in texts]
    keys = [_key(t) for t in texts]
    con = sqlite3.connect(DB)
    con.execute("CREATE TABLE IF NOT EXISTS emb (k TEXT PRIMARY KEY, v BLOB)")
    have = {}
    uniq = list(dict.fromkeys(keys))
    for i in range(0, len(uniq), 900):
        part = uniq[i : i + 900]
        q = f"SELECT k, v FROM emb WHERE k IN ({','.join('?' * len(part))})"
        have.update({k: np.frombuffer(v, dtype=np.float32) for k, v in con.execute(q, part)})
    todo = {k: t for k, t in zip(keys, texts) if k not in have}
    if todo:
        ks, ts = list(todo), list(todo.values())
        vecs = model().encode(["passage: " + t for t in ts], batch_size=batch_size, normalize_embeddings=True,
                              show_progress_bar=len(ts) > 500, convert_to_numpy=True).astype(np.float32)
        con.executemany("INSERT OR REPLACE INTO emb VALUES (?, ?)", [(k, v.tobytes()) for k, v in zip(ks, vecs)])
        con.commit()
        have.update(dict(zip(ks, vecs)))
    con.close()
    return np.stack([have[k] for k in keys]) if keys else np.zeros((0, 768), dtype=np.float32)
