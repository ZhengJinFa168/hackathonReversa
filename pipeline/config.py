"""Paths, identifiers, thresholds and model names shared by every stage."""
from __future__ import annotations

import os
import uuid
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

# --- procedure -------------------------------------------------------------
PROCEDURE = "2021/0106(COD)"
NULL_PROCEDURE = "2020/0361(COD)"  # DSA, used for the S08 null distribution
PUB_IDS = {"A": 13340, "B": 14488}
TRACK_OF_PUB = {13340: "A", 14488: "B"}

# --- paths -----------------------------------------------------------------
SOURCES_YAML = ROOT / "data_sources" / "sources.yaml"
DATA = ROOT / "data"
CACHE = DATA / "cache"          # parquet tables written by loaders (S01-S04, S06)
FEATURES = DATA / "features"    # embeddings / tfidf caches (S05)
RESULTS = DATA / "results"      # analysis CSVs (S10), graph.json, dashboard
LLM_CACHE = DATA / "llm_cache.sqlite"
LLM_COST_LOG = DATA / "llm_costs.csv"
NEO4J_DIR = ROOT / "neo4j"
QUERIES = NEO4J_DIR / "queries"
EVAL = ROOT / "eval"
for _p in (DATA, CACHE, FEATURES, RESULTS):
    _p.mkdir(parents=True, exist_ok=True)

# --- neo4j -----------------------------------------------------------------
NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "")
BATCH = 1000

# --- LLM (OpenRouter) ------------------------------------------------------
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
LLM_MODEL = os.getenv("LLM_MODEL", "deepseek/deepseek-v4-flash")
LLM_MODEL_SECOND = os.getenv("LLM_MODEL_SECOND", "google/gemini-2.5-flash-lite")
LLM_MODEL_HARD = os.getenv("LLM_MODEL_HARD", "deepseek/deepseek-v4-pro")
LLM_CONCURRENCY = int(os.getenv("LLM_CONCURRENCY", "12"))
LLM_MAX_PAIRS_PER_TRACK = 2000
JUDGE_MAX_TOKENS = 300
STANCE_MAX_TOKENS = 800
LLM_SEED = 42

# --- embeddings / matching -------------------------------------------------
EMBED_MODEL = "intfloat/multilingual-e5-base"
CHUNK_MIN_WORDS, CHUNK_MAX_WORDS = 80, 150
STOPLIST_N = 6            # proposal word n-gram stoplist
VERBATIM_RUN = 12         # longest identical run (words) => verbatim
TOPK_EMBED = 10
SCORE_WEIGHTS = {"s_embed": 0.5, "s_tfidf": 0.3, "s_ngram": 0.2}
SIMILAR_TO_COS = 0.9
NULL_PERCENTILE = 99
TIER_WEIGHT = {"T1": 3.0, "T2": 2.0, "T3": 1.0}

# --- run id ----------------------------------------------------------------
RUN_ID = os.getenv("RUN_ID") or datetime.utcnow().strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:4]
