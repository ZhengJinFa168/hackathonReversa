# Implementation plan: lobby comments → AI Act text (graph-first, Neo4j)

**Question.** Do stakeholder comments on the AI Act (procedure `2021/0106(COD)`) show up in the legal text, and through whom?

- **Track A (pre-bill):** inception-impact-assessment comments (Have Your Say `publicationId=13340`, Jul–Sep 2020) compared with the **Commission proposal** COM(2021)206 (Apr 2021).
- **Track B (post-bill):** comments on the proposal (`publicationId=14488`, Apr–Aug 2021) compared with the **MEP committee amendments** (2022), then the **EP adopted text** (June 2023), then the **final act**, Regulation (EU) 2024/1689.

**Approach: graph-first.** Neo4j is the working model and the single source of truth. Every source is loaded as nodes and relationships. Python does the numeric work (similarity, and LLM judging with a **local open-source model via Ollama**) and writes its results back into the graph as relationships. Analysis runs as Cypher queries and Graph Data Science (GDS) algorithms, and the dashboard reads exports from the graph.

```
 sources ──► Python loaders ──► Neo4j backbone graph ──► Python similarity / LLM ──► ECHOED_IN edges ──► Neo4j
                                        │                                                              │
                                        └──────────── Cypher + GDS analyses ◄──────────────────────────┘
                                                              │
                                              exports (CSV/JSON) ──► dashboard HTML · Neo4j Browser/Bloom (live demo)
```

---

## 0. Data sources

All inputs are listed in **`data_sources/sources.yaml`** with a `status`:
- **available:** the file is loaded.
- **placeholder:** a header-only CSV with the final schema. The loader returns an empty table and logs `PLACEHOLDER: <name>`, and dependent steps use their fallback. To plug in real data, put the file at the same path and set `status: available`.

| Source | Status | Path | Feeds (graph) | Fallback / note |
|--------|--------|------|---------------|-----------------|
| ParlTrack committee amendments | ✅ available | `amendments/ep_amendments.json.zst` | `Amendment`, `MEP`, `Group`, `Committee`, `Provision` | n/a |
| ParlTrack plenary amendments | ✅ available | `amendments/ep_plenary_amendments.json.zst` | `AdoptedAmendment` | n/a |
| ParlTrack MEPs | ✅ available | `amendments/ep_meps.json.zst` | `MEP`, `MEMBER_OF` | n/a |
| Have Your Say 13340 (98 contributions) | ✅ available | `WebScraping/feedback/stage1_inception_13340/parsed_feedback_stage1_13340.json` | `Organisation`, `Comment`, `Chunk` | n/a |
| Have Your Say 14488 (237 contributions) | ✅ available | `WebScraping/feedback/stage2_proposal_14488/parsed_feedback_stage2_14488.json` | `Organisation`, `Comment`, `Chunk` | n/a |
| **LobbyFacts** (17,491 registered orgs) | ✅ available | `data_sources/lobbyfacts/lobbyfacts_result.csv` | `Organisation` properties | Snapshot of **2026-10-03** (today's register, not 2021). See S06. |
| **EUR-Lex final act**, consolidated `02024R1689-20260727` | ✅ raw HTML | `data_sources/eurlex/raw/02024R1689-20260727.html` → units: `data_sources/eurlex/final_units.csv` (S02) | `LegalUnit`, `BECAME` | Consolidated texts have **no recitals** and include later amendment M1 |
| EUR-Lex final act as adopted `32024R1689` | ✅ raw HTML | `data_sources/eurlex/raw/32024R1689.html` | `LegalUnit` (recitals, "as adopted" check) | n/a |
| EUR-Lex proposal COM(2021)206 (`52021PC0206`) + annexes | ✅ raw HTML | `data_sources/eurlex/raw/52021PC0206.html`, `52021PC0206_annexes.html` → units: `data_sources/eurlex/proposal_units.csv` (S02) | `Provision` text | n/a |

**Source notes**
- **LobbyFacts columns:** `Identification code` (Transparency Register ID), `Name`, `Members FTE`, `Lobbying cost` (EUR), `Interest represented`, `Head office`, `EU office`, `EP passes on 2026-10-03`, `all EP passes`, `Meetings` (number of Commission high-level meetings, all-time aggregate), `Lobbyfacts URL`.
  - **Join key = the Transparency Register number that submitters entered on Have Your Say** (`trNumber` in the raw feedback JSON). 81 of 98 contributions (13340) and 168 of 237 (14488) carry one. After cleaning, 70 and 140 of them join LobbyFacts directly. No name matching is needed for these. See S06.
  - The figures are current, not 2021. State this on every chart that uses them. If time allows, re-download the snapshot closest to 2021 from LobbyFacts' history.
- **EUR-Lex:** the website blocks scripts (an empty `202`), so the texts were downloaded through the Publications Office **CELLAR API**: `http://publications.europa.eu/resource/celex/<CELEX>` with `Accept: text/html`. For the proposal, the API returns a list of documents; act = `DOC_1`, annexes = `DOC_3`. The raw HTML is in `data_sources/eurlex/raw/`, and S02 parses it into units.
  - The consolidated version (27.07.2026) has **no recitals**, because consolidated texts omit them. It also contains a later amendment, Regulation (EU) 2026/1744 of 8 July 2026 (marked `►M1`, 69 places), so a few provisions differ from what was adopted in 2024. Use the consolidated text for articles and annexes "as in force today", and `32024R1689` for recitals and for the "as adopted" check. Flag units whose wording comes from M1.
- **Integrity Watch EU:** dropped, because it can't be downloaded. There are no dated MEP or Commission meeting edges. The only access signal left is LobbyFacts' aggregate `Meetings` count and EP passes, stored as properties.

**Placeholder rules**
- One loader, `pipeline/sources.py: load(name)`, checks columns against the schema and returns an empty typed frame for placeholders.
- Stages record `degraded: [sources]`. The dashboard shows a "Data coverage" box.
- Placeholder files stay header-only. No invented rows.

---

## 1. Graph model (Neo4j)

### Nodes

| Label | Key (unique constraint) | Properties | Loaded in |
|-------|-------------------------|------------|-----------|
| `Organisation` | `org_id` (= `tr_id` when matched, else `hys:<normalised name>`) | name, user_type, country, company_size, tr_id, lobbying_cost_eur, fte, interest_represented, head_office, ep_passes_now, ep_passes_all, ec_meetings_all, lobbyfacts_url, match_method, match_score | S01 + S06 |
| `Comment` | `feedback_id` | pub_id (13340/14488), track (A/B), date, language, n_words, has_attachment, url | S01 |
| `Chunk` | `chunk_id` | text, idx, n_words, cited_articles | S01 |
| `Provision` | `unit_id` (`Art5(1)(d)`, `Rec27`, `AnnexIII(1)(5)(b)`) | kind, article, path, text (Commission proposal) | S02 / S03 |
| `Amendment` | `am_id` (`PE731.563-123`) | committee, date, location, old_text, new_text, added_text, is_new, deletion, survival_score, survived, reached_law | S03 |
| `AdoptedAmendment` | `adopted_id` (`A9-0188/2023-n`) | text, added_text, date | S04 |
| `LegalUnit` | `law_unit_id` (`2024/1689:Art5(1)(h)`) | kind, article, path, text, consolidated_version | S02 |
| `MEP` | `mep_id` (ParlTrack UserID) | name, country | S03 |
| `Group` | `group_id` (PPE, S&D, RE…) | label | S03 |
| `Committee` | `code` (IMCO-LIBE, JURI…) | | S03 |
| `Issue` | `issue_id` (Track A codebook) | label, proposal_choice, article_ref | S09 |

### Relationships

| Type | Pattern | Properties | Created in |
|------|---------|------------|------------|
| `SUBMITTED` | `(Organisation)-[:SUBMITTED]->(Comment)` | date | S01 |
| `PART_OF` | `(Chunk)-[:PART_OF]->(Comment)` | idx | S01 |
| `CITES` | `(Comment)-[:CITES]->(Provision)` | count | S01 |
| `AMENDS` | `(Amendment)-[:AMENDS]->(Provision)` | is_new, deletion | S03 |
| `TABLED` | `(MEP)-[:TABLED]->(Amendment)` | weight = 1/n co-signers | S03 |
| `MEMBER_OF` | `(MEP)-[:MEMBER_OF]->(Group)` | from, to | S03 |
| `IN_COMMITTEE` | `(Amendment)-[:IN_COMMITTEE]->(Committee)` | | S03 |
| `SURVIVED_AS` | `(Amendment)-[:SURVIVED_AS]->(AdoptedAmendment)` | score | S04 |
| `BECAME` | `(Provision｜AdoptedAmendment)-[:BECAME]->(LegalUnit)` | score | S04 |
| **`ECHOED_IN`** | `(Chunk)-[:ECHOED_IN]->(Provision)` (Track A) or `->(Amendment)` (Track B) | score, s_embed, s_tfidf, s_ngram, longest_run, verbatim, llm_relation, llm_direction, overlap_quote, tier | S07–S09 (above threshold only) |
| `TOOK_POSITION` | `(Organisation)-[:TOOK_POSITION]->(Issue)` | stance, quote, aligned (bool) | S09 |
| `SIMILAR_TO` | `(Chunk)-[:SIMILAR_TO]->(Chunk)` | score (near-duplicates across orgs → coalitions) | S07 |

**Evidence tiers on `ECHOED_IN`** (no meeting data):
- **T1:** verbatim (≥12 identical words after the stoplist) **and** LLM says `implements`.
- **T2:** verbatim, or LLM says `implements` or `partial`.
- **T3:** semantic score above threshold only.

Separately, an echo also has **reach**: does the path continue to `AdoptedAmendment` and to `LegalUnit`?

---

## 2. Repo layout and conventions

```
hackathonReversa/
  WebScraping/                   # existing: download_all_feedback.py → parse_and_clean_feedback.py → feedback/*/parsed_*.json
  amendments/                    # existing: ParlTrack dumps, dashboard, exploration notebook
  data_sources/                  # committed: sources.yaml; lobbyfacts/ (register export), eurlex/raw (CELLAR HTML), *_units.csv written by S02
  neo4j/
    docker-compose.yml           # neo4j:5 + APOC + GDS plugins, ports 7474/7687, volume ./neo4j/data (gitignored)
    schema.cypher                # constraints + indexes
    queries/                     # named analysis queries (*.cypher), used by S10 and the demo
  pipeline/
    config.py                    # paths, PROCEDURE, PUB_IDS, thresholds, model names
    sources.py                   # load(name) with placeholder handling
    graph.py                     # Neo4j driver wrapper: merge_nodes(label, key, rows), merge_rels(...), batched UNWIND
    s00_check.py                 # sanity check (sources, counts, Neo4j reachable)
    s01_feedback.py              # → Organisation, Comment, Chunk, SUBMITTED, PART_OF, CITES
    s02_legal_texts.py           # EUR-Lex HTML → Provision text, LegalUnit
    s03_amendments.py            # ParlTrack → Amendment, MEP, Group, Committee, AMENDS, TABLED, MEMBER_OF
    s04_survival.py              # plenary + final act → AdoptedAmendment, SURVIVED_AS, BECAME
    s05_features.py              # reads Chunk/target text from Neo4j; stoplist, TF-IDF, embeddings (cached .npy)
    s06_orgs.py                  # LobbyFacts entity resolution → Organisation properties, merge duplicates
    s07_match.py                 # candidates + scores → candidate table (parquet)
    s08_calibrate.py             # null distribution → thresholds.json; writes ECHOED_IN above threshold
    s09_judge_and_stance.py      # LLM judge → ECHOED_IN props + tiers; Track A stance → TOOK_POSITION
    s10_analyse.py               # runs neo4j/queries/*.cypher + GDS → data/results/*.csv
    s11_export_dashboard.py      # graph.json + results → dashboard HTML
    s12_eval.py                  # precision on eval/labels.csv
    run.py                       # python -m pipeline.run --from s03 --track B
  data/                          # gitignored: parquet caches, embeddings, exports
  eval/labels.csv
  .env                           # gitignored: NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD, OLLAMA_HOST, LLM_MODEL
```

**Neo4j setup.** Use Docker with `neo4j:5`, `NEO4J_PLUGINS='["apoc","graph-data-science"]'`, and auth from `.env`. Neo4j Desktop works too. Neo4j Aura Free works for sharing, but it has no GDS, so community detection would run in Python there.

**Conventions**
- Every load is idempotent. Use `MERGE` on the unique key and batched `UNWIND $rows` of 1,000. Re-running a stage updates the graph instead of duplicating it.
- `schema.cypher` creates a uniqueness constraint per node key before any load.
- Each stage stamps the nodes and relationships it creates with `source` and `run_id`, so a stage can be wiped and reloaded (`MATCH ()-[r:ECHOED_IN {run_id:$old}]->() DELETE r`).
- Heavy numeric work happens outside Neo4j: embeddings, the n-gram index and TF-IDF are cached in `data/`. Only results go into the graph.
- Requirements: `neo4j` (driver), `pandas`, `pyarrow`, `scikit-learn`, `sentence-transformers`, `rapidfuzz`, `langdetect`, `ollama` (Python client), `pyyaml`, plus the PDF library `WebScraping/` already uses.

---

## 3. Stages

### Layer 1: backbone graph (deterministic loads)

**S00 Sanity check**
- Print every source in `sources.yaml` as available or PLACEHOLDER, with row counts and a schema check.
- Feedback files: counts per user_type (COMPANY, BUSINESS_ASSOCIATION, NGO, TRADE_UNION, CONSUMER_ORGANISATION), no duplicate IDs, fewer than 10% empty texts.
- ParlTrack: 4,852 committee and 771 report amendments for the procedure.
- Neo4j is reachable, the plugins are present, and `schema.cypher` is applied.
- Warn only; never block.

**S01 Feedback → `Organisation`, `Comment`, `Chunk`**
- Load the parsed JSONs for 13340 (Track A) and 14488 (Track B).
- Re-chunk to **paragraphs of about 80–150 words** with a one-sentence overlap. The current 400-word chunks are about 5× an amendment's length and dilute similarity. Keep the 400-word chunks for the LLM context.
- Strip boilerplate (letterheads, addresses, "we welcome the opportunity…"), detect language, and parse `cited_articles` into `CITES` edges.
- `Organisation` is keyed provisionally by normalised name. S06 merges in register IDs.

**S02 Legal texts (EUR-Lex) → `Provision` text, `LegalUnit`**
- Parse the raw HTML in `data_sources/eurlex/raw/`: the proposal (`52021PC0206` + annexes), the consolidated final act (`02024R1689-20260727`) and the original act (`32024R1689`, for recitals) into units: recitals, articles with paragraphs and points, annex points. Write them to `data_sources/eurlex/*_units.csv`.
- Normalise IDs to ParlTrack's `location` strings (`Article 5 – paragraph 1 – point d` → `Art5(1)(d)`).
- Test: the ParlTrack `old` texts should match the proposal units almost exactly.

**S03 Committee amendments → `Amendment`, `MEP`, `Group`, `Committee`**
- Reuse `amendments/build_dashboard.py` for the filter, line joining, MEP → group at the tabling date, and the word diff.
- `added_text` holds **only the inserted words**. It's the Track B matching target. Deletions keep `old_text` and set `deletion = true`.
- `TABLED` weight = 1/n co-signers, so group-wide amendments don't dominate centrality.

**S04 Survival → `AdoptedAmendment`, `SURVIVED_AS`, `BECAME`**
- Use the 771 report amendments `A9-0188/2023-1..771` from the plenary dump.
- For each committee amendment, compare it with adopted amendments on the same `unit_id`. Combine fuzzy token-set ratio with embedding cosine into `survival_score`. Create `SURVIVED_AS` when the score is ≥ τ_s **and** the pair is a mutual best match. τ_s is set automatically, with no hand-checking: it is the 99th percentile of a null distribution, i.e. the same committee amendments scored against adopted amendments on *other* provisions. Compromises merge amendments, so keep the score as well as the flag.
- Map adopted text and provisions to `LegalUnit` by text similarity (the final act was renumbered) to create `BECAME`. Then set `Amendment.reached_law`.

**S06 Organisations (LobbyFacts) → `Organisation` properties, fully automatic**
No hand-checking. Every match carries a `match_method` and a `match_confidence`, and anything uncertain is left unmatched rather than guessed.
1. **Register number (primary key, confidence 1.0).**
   - Take `trNumber` from `WebScraping/feedback/*/raw_feedback_*.json`. Submitters enter it themselves.
   - Clean it by extracting the regex `\d{6,15}-\d{2}`. That strips spaces, a "TR"/"ID#" prefix, zero-width characters and trailing dots.
   - Join on LobbyFacts `Identification code`. This covers 70 of 98 contributions (13340) and 140 of 237 (14488).
2. **Register number not in the current LobbyFacts file** (about 11 + 28 contributions; the org has probably left the register since 2021).
   - Keep `tr_id` as the node key, and set `in_register_now = false`.
   - Optional automatic enrichment: fetch the archived LobbyFacts datacard by `rid`.
   - Otherwise the money properties stay null.
3. **No register number** (17 + 69 contributions). Automatic name matching, tuned for precision:
   - **3a.** Exact match on the normalised name: lowercase, ASCII, legal forms removed (e.V., AISBL, SA, Ltd, GmbH, AG, SARL…), bracketed parts removed. Confidence 0.9.
   - **3b.** Acronym match: an acronym given in the Have Your Say name equals the register name's acronym or leading token (e.g. "BEUC", "ETNO"), **and** the head-office country matches. Confidence 0.8.
   - **3c.** `rapidfuzz` token-set ratio ≥ 95, **and** the same country, **and** a compatible category. For category, Have Your Say `COMPANY` or `BUSINESS_ASSOCIATION` must map to an "own or members' interests" register entry. The best candidate must also beat the second-best by ≥ 5 points. Confidence 0.7.
   - Anything else stays **unmatched**: no LobbyFacts properties, `match_method = none`.
   - Optional, still automatic: an LLM tie-break for 3c candidates scoring 85–95. Give it both names, countries and websites, and accept only a confident "same entity".
4. **Deduplicate:** `MERGE` organisation nodes on `tr_id`, or on the normalised name when there is no ID, with `apoc.refactor.mergeNodes`. This fixes cases like "NL AIC" ×3.
5. **Set properties:** `lobbying_cost_eur, fte, interest_represented, ep_passes_now, ep_passes_all, ec_meetings_all, lobbyfacts_url, in_register_now, match_method, match_confidence`.

**How the analysis handles unchecked matches**
- Report coverage by user_type and by method in the data-coverage box, e.g. "register number 210 · name match n · unmatched n".
- Money-vs-influence uses matched orgs only. **Run a sensitivity check:** repeat it using only register-number matches (confidence 1.0). If the result holds, name matching isn't driving it.
- Echo, reach and graph analyses don't depend on LobbyFacts. They run on every organisation, matched or not.
- Never impute zero spend for unmatched orgs. Leave it null.

### Layer 2: echo edges (computed in Python, written into the graph)

**S05 Features**
- Read chunk and target texts from Neo4j (or from the parquet caches written by S01–S03).
- **Proposal stoplist:** remove every word 6-gram that appears in the proposal before n-gram matching. Comments and amendments both quote the proposal, so this is the most important trick.
- TF-IDF on word 1–2-grams. Embeddings with `intfloat/multilingual-e5-base`, cached as `.npy` by text hash.

**S07 Matching (candidates and scores)**
- **Candidates** per chunk:
  - top-10 targets by embedding cosine;
  - any target sharing a non-stoplisted 6-gram;
  - for Track B, amendments on articles the comment `CITES` (a Cypher lookup).
- **Scores:** `s_embed`, `s_tfidf`, `s_ngram` and `longest_run`, combined as `score = 0.5·z(s_embed) + 0.3·z(s_tfidf) + 0.2·z(s_ngram)`. Set `verbatim = longest_run ≥ 12`.
- **Targets:** Track A → `Provision` (proposal text). Track B → `Amendment.added_text`.
- **Near-duplicates:** chunk-to-chunk pairs with cosine ≥ 0.9 across different orgs → `SIMILAR_TO`, which signals a coalition or shared position paper.

**S08 Calibration, then write `ECHOED_IN`**
- Null distribution: score the same chunks against an unrelated procedure (the DSA, `2020/0361(COD)`, same ParlTrack dump) and against shuffled targets.
- `τ_match` = 99th percentile of the null. Check that `verbatim` produces about 0 null hits.
- **Only pairs above `τ_match`, plus all verbatim pairs, become `ECHOED_IN` relationships.** That keeps the graph readable and meaningful.

**Local LLM setup (open-source, no API).** Every LLM step (judge, stance coding, proposal coding, optional translation and org tie-break) uses an open-weights model served locally by **[Ollama](https://ollama.com)** on the laptop.
- **Install:** `brew install ollama`, then `ollama serve`, then `ollama pull qwen3:8b`. The API is at `http://localhost:11434`, and it also has an OpenAI-compatible `/v1` endpoint.
- **Model choice for a 16 GB Apple M4:**

  | Role | Model | Why |
  |------|-------|-----|
  | Default | `qwen3:8b` (Apache-2.0, about 5 GB at Q4) | Strong at following instructions and producing JSON, multilingual, fits beside Neo4j |
  | Second opinion | `llama3.1:8b` (Llama licence) or `gemma3:12b` (about 8 GB) | Cross-model agreement check in S12 |
  | Avoid on this laptop | 24B+ models | Too slow, and memory-tight next to Neo4j and embeddings |

- **Structured output:** pass a JSON schema in Ollama's `format` parameter, so every answer parses. Use `temperature 0` and a fixed `seed`. Turn Qwen3's thinking mode off (`/no_think`) for the judge, because it's faster and the reasoning is short anyway.
- **Throughput budget** (8B Q4 on M4): about 4 s per judged pair with a short prompt (≈150-word chunk + ≈100-word amendment + Commission text). That's roughly 1,000 pairs per hour. **Cap the judge at about 600 pairs per track**, the top `ECHOED_IN` by score plus all verbatim pairs. Run it as a background batch and cache every answer by prompt hash in `data/llm_cache.sqlite`, so re-runs and the demo are instant.
- **Memory:** give Neo4j a 2 GB heap in docker-compose (`NEO4J_server_memory_heap_max__size=2G`), compute embeddings before the LLM batch, and don't run both at once.
- Config lives in `.env`: `LLM_MODEL=qwen3:8b`, `OLLAMA_HOST=http://localhost:11434`. Swapping the model is a one-line change.

**S09 LLM judge and stance coding**
- **Judge (both tracks):**
  - Input: the `ECHOED_IN` pairs, with the org name hidden.
  - Local `qwen3:8b` through Ollama with a JSON schema, `temperature 0`, cached by prompt hash. Run it as a background batch, capped at about 600 pairs per track.
  - It sets `llm_relation` (implements, partial, contradicts or unrelated), `llm_direction` (strengthens, weakens or neutral) and `overlap_quote`, then computes `tier` (T1/T2/T3).
  - Relations judged `unrelated` are kept, but flagged and hidden by default.
- **Track A stance:**
  - The 13340 comments argue about policy options, so an LLM codes each org's stance on about 10 `Issue` nodes:
    - option 1–4;
    - AI definition;
    - remote biometric ID;
    - self vs third-party conformity assessment;
    - closed vs open high-risk list;
    - sandboxes;
    - SME relief;
    - liability;
    - enforcement;
    - standards.
  - **`Issue.proposal_choice` is coded automatically** with the same codebook and model. For each issue, the LLM reads the relevant proposal text, retrieved from the `Provision` nodes (e.g. Art. 3 + Annex I for the definition, Art. 5 for biometrics, Art. 43 for conformity assessment, Art. 6–7 + Annex III for the high-risk list, Art. 53–55 for sandboxes and SMEs, Art. 40–41 for standards, Art. 59 for enforcement). It returns the choice, the article it relied on, and a quote. Run it 3 times with different seeds and keep the majority vote; log any issue where the runs disagree. Liability isn't in the AI Act, so it is coded "not addressed".
  - Long contributions are split into ≤6k-token parts and coded per part, then merged. A stance found in any part counts, and conflicts are kept as `mixed`.
  - Write the result as `TOOK_POSITION {stance, quote, aligned}`.

### Layer 3: analysis in the graph (Cypher + GDS), S10

Named queries live in `neo4j/queries/` and are exported to `data/results/*.csv`:
1. **Influence score per organisation:** the sum over its paths `Organisation→Comment←Chunk→ECHOED_IN→Amendment` of `score × tier weight × (1 + survived + reached_law)`.
2. **Reach funnel:** chunks → echoes → echoes whose amendment survived → echoes that reached the law. Split by user_type, compared with the base rate of all amendments. **This is the headline number.**
3. **Carriers:** Org–MEP projection, i.e. which MEPs tabled amendments echoing which organisations, and in which group.
4. **Centrality (GDS):** PageRank on Org→Amendment→Provision (most influential orgs, most contested provisions), and betweenness on MEPs (brokers between lobbies and adopted text).
5. **Coalitions (GDS):** Node Similarity or Louvain on the Org–Org graph (co-echoed in the same amendments, or `SIMILAR_TO` chunks), compared with user_type.
6. **Battlegrounds:** provisions receiving `ECHOED_IN` with opposite `llm_direction` from different user_types (e.g. Art. 5 biometrics, industry vs NGOs).
7. **Money vs influence:**
   - Compare influence score with `lobbying_cost_eur`, `fte`, `ep_passes_all` and `ec_meetings_all` (LobbyFacts).
   - Use Spearman correlation with a confidence interval, split by user_type. Label it "2026 register figures".
8. **Track A alignment:** the share of an org's `TOOK_POSITION` stances matching `proposal_choice`, by user_type, compared with a random-stance baseline.

Example (Track B, the full chain):
```cypher
MATCH (o:Organisation)-[:SUBMITTED]->(:Comment)<-[:PART_OF]-(c:Chunk)
      -[e:ECHOED_IN]->(a:Amendment)<-[:TABLED]-(m:MEP)-[:MEMBER_OF]->(g:Group)
WHERE e.tier IN ['T1','T2'] AND a.reached_law
OPTIONAL MATCH (a)-[:SURVIVED_AS]->(:AdoptedAmendment)-[:BECAME]->(l:LegalUnit)
RETURN o.name, o.user_type, o.lobbying_cost_eur, a.am_id, m.name, g.group_id, l.law_unit_id, e.score, e.overlap_quote
ORDER BY e.score DESC LIMIT 25;
```

### Layer 4: output

**S11 Dashboard and demo**
- **Live demo, all running locally on the laptop with no internet needed:**
  1. **Neo4j Browser or Bloom** with saved perspectives (organisation → amendment → MEP → law), plus 3–4 prepared Cypher queries from `neo4j/queries/`.
  2. **"Judge this pair live":** a button in the pair viewer sends the lobby chunk and the amendment to the local Ollama model (`fetch('http://localhost:11434/api/chat')`, streaming) and shows the verdict and overlapping quote appearing in real time. This shows it's an open model on the laptop, not a cloud API.
     - Start Ollama with `OLLAMA_ORIGINS=*`, or the dashboard's origin, so the page may call it.
     - If the call fails, the button falls back to the cached verdict.
  3. **Optional "Ask the graph":** a question in plain language goes to the local LLM, which writes Cypher from the schema, runs it on Neo4j, and shows the query and the result. This is text-to-Cypher with an 8B model, so keep 3 rehearsed questions and their prepared queries as a fallback.
  - **Before the demo:** run all batches and warm the cache. Pre-load the model with `ollama run qwen3:8b ""` so the first live call isn't slow. Close other apps, because 16 GB is shared by Neo4j, the browser and the model.
- **Offline dashboard** (one self-contained HTML file, extending `amendments/build_dashboard.py`). It's built from the `data/results/*.csv` files and `graph.json`, an export of the T1/T2 subgraph.
  - KPI tiles and the reach funnel.
  - Network view (sigma.js), filterable by track, user_type, article, tier and reached-law.
  - Sankey: user_type → org (top 20) → article → survived / reached law.
  - Pair viewer: the lobby chunk next to the amendment, shared phrases highlighted, the LLM verdict and quote.
  - Organisation profile card (LobbyFacts figures and echoes by tier), MEP carrier view, money-vs-influence scatter, and the Track A issue heatmap.
  - Method and data-coverage box.

**S12 Evaluation**
Automatic, with no hand-labelling required:
- **Cross-model agreement:** a second open model (`llama3.1:8b` or `gemma3:12b`) re-judges a stratified sample of 200 `ECHOED_IN` pairs per track (top, near-threshold, random). Report Cohen's κ between the two models. Pairs where they disagree are downgraded one tier.
- **Null check:** the share of null pairs (unrelated DSA amendments) that the judge labels `implements`. This is the judge's false-positive rate, and it should be near 0.
- **Verbatim sanity:** every verbatim pair should be judged `implements` or `partial`. Report the rate.
- **Stability:** for stance coding, report agreement across the 3 seeded runs.
- Optional, if someone has 20 minutes: label 30 pairs in `eval/labels.csv` for a human precision figure.

---

## 4. Order of work and split (about 10 h, 3 people)

| Phase | Time | Person A (graph and data) | Person B (matching) | Person C (analysis and story) |
|------|------|---------------------------|---------------------|-------------------------------|
| 0 | 0:30 | Neo4j via docker-compose, `schema.cypher`, `graph.py` | `pipeline/` skeleton, config, S00; install Ollama + `ollama pull qwen3:8b` | Sketch the views, write the demo story |
| 1 | 2:00 | S01 + S03 loaders → backbone graph | S05 features (stoplist, TF-IDF, embeddings) | S09 codebook, code `proposal_choice` |
| 2 | 3:00 | S02 EUR-Lex units, S04 survival, S06 automatic LobbyFacts join | S07 matching, S08 calibration → `ECHOED_IN` | First Cypher queries on the backbone |
| 3 | 2:00 | Track B end-to-end on the graph | S09 judge + stance (local Ollama, background batch), S12 automatic checks | S10 GDS analyses, results CSVs |
| 4 | 2:00 | Exports (`graph.json`), Bloom perspective | Precision numbers, threshold tuning | S11 dashboard and demo script |

**Track A first** proves the whole loop. Track B then reuses every stage with a different target.

**Minimum viable demo:** the backbone graph, then S07 (n-gram and embeddings) writing `ECHOED_IN`, then one Cypher query plus Neo4j Browser and the pair viewer, showing 5 striking verbatim lobby → amendment → law chains.

## 5. Risks and mitigations

| Risk | Mitigation |
|------|------------|
| Proposal quotes inflate similarity | 6-gram proposal stoplist; match only `added_text` |
| Hairball graph | Only above-threshold `ECHOED_IN`; default view T1/T2; `TABLED` weighted 1/n |
| Neo4j setup eats time | docker-compose with plugins pinned; Aura Free as fallback (GDS-free, do communities in Python) |
| Duplicate org nodes | Merge on register number / normalised name with `apoc.refactor.mergeNodes` |
| Wrong automatic org matches (no hand-check) | Register numbers first (exact IDs); strict name rules (≥95, same country, compatible category, clear winner); leave ambiguous ones unmatched; sensitivity check on ID-only matches |
| LobbyFacts figures are 2026, not 2021 | Label charts; optionally pull a historical snapshot |
| EUR-Lex website blocks scripts | Use the CELLAR API (already done; raw HTML in `data_sources/eurlex/raw/`) |
| Consolidated act includes later changes | Flag post-2024 wording; prefer `32024R1689` for "as adopted" |
| No meeting data | Tiers rely on text evidence only; access proxied by LobbyFacts aggregates, stated as such |
| Compromise AMs blur survival | Keep `survival_score`, report partial survival |
| Similarity ≠ causation | Call matches "echoes", show the null baseline, the base rate and the timeline |
| Local LLM is slow (8B on a laptop) | Short prompts, cap about 600 pairs per track, background batch, prompt-hash cache, start the batch early (phase 2) |
| 8B judge less accurate than frontier models | JSON schema, temperature 0, the judge only refines already-thresholded pairs, cross-model agreement, null false-positive rate reported |
| Memory pressure (16 GB) | Neo4j heap 2 GB, don't run embeddings and the LLM at the same time, an 8B Q4 model only |

## 6. Definition of done
- `docker compose up` plus `python -m pipeline.run --track A` and `--track B` rebuild the graph from clean in under 30 minutes, excluding the local LLM batch (about 1–2 h, cached after the first run).
- `neo4j/queries/` holds the named analyses, and `data/results/` holds the CSVs, `graph.json` and the dashboard HTML.
- Precision at threshold is reported. The README explains the method, data coverage and caveats on one page.
