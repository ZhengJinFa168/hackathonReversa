# Implementation plan: lobby comments → AI Act text (graph-first, Neo4j)

**Question.** Do stakeholder comments on the AI Act (procedure `2021/0106(COD)`) show up in the legal text, and through whom?

- **Track A (pre-bill):** inception-impact-assessment comments (Have Your Say `publicationId=13340`, Jul–Sep 2020) compared with the **Commission proposal** COM(2021)206 (Apr 2021).
- **Track B (post-bill):** comments on the proposal (`publicationId=14488`, Apr–Aug 2021) compared with the **MEP committee amendments** (2022), then the **EP adopted text** (June 2023), then the **final act**, Regulation (EU) 2024/1689.

**Approach: graph-first.** Neo4j is the working model and the single source of truth. Every source is loaded as nodes and relationships. Python does the numeric work (similarity, and LLM judging through the **OpenRouter API**) and writes its results back into the graph as relationships. Analysis runs as Cypher queries and Graph Data Science (GDS) algorithms, and the dashboard reads exports from the graph.

```
 sources ──► Python loaders ──► Neo4j backbone graph ──► Python similarity / LLM ──► ECHOED_IN edges ──► Neo4j
                                        │                                                              │
                                        └──────────── Cypher + GDS analyses ◄──────────────────────────┘
                                                              │
                                              exports (CSV/JSON) ──► dashboard HTML · Neo4j Browser/Bloom (live demo)
```

### Status (updated 2026-10-03)

**Done**
- **Data sources & scraping pipeline:** Have Your Say feedback for Stage 1 (13340 - Inception Impact Assessment, 98 contributions) and Stage 2 (14488 - Proposal for a Regulation, 237 contributions) fully downloaded, parsed, cleaned, and chunked (`WebScraping/feedback/`). Corrected EU API filters to properly include NGOs and Consumer Organisations.
- **Integrity Watch EU integrated:** `data_sources/integritywatch/mep_meetings.csv` and `registered_lobby_organizations.csv` registered in `data_sources/sources.yaml`.
- **Git & Repo strategy:** Unified root `.gitignore`. Binary database directory (`neo4j/data/` / `neo4j_data/`) and attachment PDFs excluded to prevent GitHub 100MB file limit errors (e.g. 256MB Neo4j transaction logs). The graph is shared across collaborators via reproducible python loaders (`neo4j/load_data.py`) and committed JSON datasets.
- **Neo4j infrastructure & schema:**
  - `neo4j/docker-compose.yml` configured with Neo4j 5 Community and APOC.
  - `neo4j/schema.cypher` and `neo4j/apply_schema.py` defined and applied: 7 uniqueness constraints and 10+ performance indexes.
- **Backbone ingestion & entity resolution (`neo4j/load_data.py`):**
  - Ingested 290 unique `Organisation` nodes, 335 `Comment` nodes, and 2,618 `Chunk` nodes.
  - Enriched with LobbyFacts (`data_sources/lobbyfacts/lobbyfacts_result.csv`).
  - **Deduplication issue resolved:** Solved `ConstraintValidationFailed` when multiple raw organisations mapped to the same Transparency Register ID (`org_id`) via a custom Cypher node merger transferring properties and `[:SUBMITTED]` relationships before deleting duplicates.
  - **Fuzzy matching:** Integrated `rapidfuzz` (threshold >= 90) for unmatched names, successfully enriching 161 organisations with lobby costs, FTE, and EP pass metrics.
- **Parliamentary & Amendments data ingestion (`neo4j/load_parltrack.py` - S03):**
  - Ingested all 8 `PoliticalGroup` nodes (EPP, S&D, Renew, Greens/EFA, The Left, ID, ECR, NI).
  - Ingested 171 `MEP` nodes and connected them to their political group via `[:MEMBER_OF]`.
  - Ingested 4,852 `Amendment` nodes for procedure `2021/0106(COD)` (AI Act) with text diffs (`added_text`, `old_text`, `new_text`).
  - Ingested 327 `LawProvision` nodes and linked amendments via `[:TARGETS]`.
  - Created 19,059 `[:TABLED]` relationships linking MEPs to amendments with lead authorship and 1/n weight.
- **Queries & metrics:** Main analytical and verification queries stored in `neo4j/queries/metrics.cypher`.

**In Progress / Next (See Section 8 for Actionable Step-by-Step Guide)**
- **STEP 1:** Matching engine (S07): 6-gram Commission stoplist + cosine similarity -> `ECHOED_IN` relationships in Neo4j.
- **STEP 2:** Analytics & Aggregation (S10): Calculate influence scores, funnels, carriers, and export `data/dashboard_data.json`.
- **STEP 3:** Interactive Dashboard (S11): Build dark-mode SPA (HTML5/Tailwind/JS) with the 4 high-impact views (Smoking Gun side-by-side, Scatter plot, Political matrix, Hero KPIs).
- **STEP 4:** Pitch & Live Check Readiness: 5-minute demo rehearsal with Neo4j live queries answering the 5 jury questions.

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
| **Integrity Watch EU, MEP meetings** (6,123 meetings, 409 MEPs) | ✅ available | `data_sources/integritywatch/mep_meetings.csv` (`;`-separated, UTF-8 BOM, dates `dd/mm/yy`) | `MET_WITH` (Organisation → MEP) | Covers **Jan–Dec 2020 and Jun–Oct 2024 only**. See notes and S06b. |
| **Integrity Watch EU, register** (17,897 orgs) | ✅ available | `data_sources/integritywatch/registered_lobby_organizations.csv` | `Organisation` properties | Superset of LobbyFacts (all 17,491 LobbyFacts IDs + 406 more). See S06. |

**Source notes**
- **LobbyFacts columns:** `Identification code` (Transparency Register ID), `Name`, `Members FTE`, `Lobbying cost` (EUR), `Interest represented`, `Head office`, `EU office`, `EP passes on 2026-10-03`, `all EP passes`, `Meetings` (number of Commission high-level meetings, all-time aggregate), `Lobbyfacts URL`.
  - **Join key = the Transparency Register number that submitters entered on Have Your Say** (`trNumber` in the raw feedback JSON). 81 of 98 contributions (13340) and 168 of 237 (14488) carry one. After cleaning, 70 and 140 of them join LobbyFacts directly. No name matching is needed for these. See S06.
  - The figures are current, not 2021. State this on every chart that uses them. If time allows, re-download the snapshot closest to 2021 from LobbyFacts' history.
- **EUR-Lex:** the website blocks scripts (an empty `202`), so the texts were downloaded through the Publications Office **CELLAR API**: `http://publications.europa.eu/resource/celex/<CELEX>` with `Accept: text/html`. For the proposal, the API returns a list of documents; act = `DOC_1`, annexes = `DOC_3`. The raw HTML is in `data_sources/eurlex/raw/`, and S02 parses it into units.
  - The consolidated version (27.07.2026) has **no recitals**, because consolidated texts omit them. It also contains a later amendment, Regulation (EU) 2026/1744 of 8 July 2026 (marked `►M1`, 69 places), so a few provisions differ from what was adopted in 2024. Use the consolidated text for articles and annexes "as in force today", and `32024R1689` for recitals and for the "as adopted" check. Flag units whose wording comes from M1.
- **Integrity Watch EU, MEP meetings** (`epid;mep;group;country;committees;role;dossier;title;lobbyists;location;date`):
  - `epid` is the EP member ID, the same as ParlTrack `UserID`, so MEPs join directly. 55 of the 171 MEPs who tabled AI Act committee amendments appear, with 1,449 meetings between them.
  - **Time coverage is the main limit.** Rows exist only for 2020 (3,343) and Jun–Oct 2024 (2,780). Nothing covers 2021 to mid-2024, which is the whole period of committee amendments (2022), the plenary vote (June 2023) and the trilogues. So meetings can show **prior contact** (2020, around the Track A consultation) and **later contact**, never contact in the weeks before an amendment was tabled.
  - `dossier` is empty in every row, so AI Act relevance comes only from `title`. 136 meetings mention AI (112 in 2020, 24 in 2024); note that 27 of them are about "AI Civil Liability", a different file.
  - `lobbyists` is a free-text name with **no register ID**, sometimes several organisations in one cell. It needs name matching (S06b). As a rough check, 82 Have Your Say organisations appear verbatim by register name.
  - `role` (Member, Rapporteur, Shadow rapporteur, Committee chair…) says in what capacity the MEP met them. Of the AI Act rapporteurs, Benifei appears (10 meetings) and Tudorache does not; Axel Voss has 99.
- **Integrity Watch EU, register** (`Id, RegDate, Cat, Cat2, Name, Country, People, FTE, Accred, FoI, Costs, Meetings, MeetingsNumVonderleyen1, MeetingsNumVonderleyen2, MeetingsNumJuncker, MepMeetingsNum`):
  - Joins on `Id` = Transparency Register number, like LobbyFacts. 70 contributions (13340) and 142 (14488) join; 143 of the 171 matched organisations have `MepMeetingsNum > 0`.
  - It adds what LobbyFacts lacks: `RegDate` (was the org registered during the AI Act process?), `MepMeetingsNum` (declared MEP meetings, all-time), Commission meetings **split by mandate** (`MeetingsNumVonderleyen1` = 2019–2024, the AI Act period, is better than LobbyFacts' all-time `Meetings`), and `FoI` (fields of interest, e.g. "Digital economy and society").
  - `Costs` is either an exact figure or a range string (`"100 000 - 199 999"`, `"<10 000"`). Parse ranges to a midpoint and keep `costs_is_range = true`.

**Placeholder rules**
- One loader, `pipeline/sources.py: load(name)`, checks columns against the schema and returns an empty typed frame for placeholders.
- Stages record `degraded: [sources]`. The dashboard shows a "Data coverage" box.
- Placeholder files stay header-only. No invented rows.

---

## 1. Graph model (Neo4j)

This section is canonical. The draft in `docs/neo4j_graph_schema.md` was reconciled with it as follows:
- **Taken from the draft:** the dated `MET_WITH` edge from Integrity Watch; `TABLED.is_lead_author`; an optional `Chunk.embedding` property with a Neo4j vector index, so similar chunks can be found inside Neo4j for the demo (S07 still computes scores in Python).
- **Kept from this plan:** the separate `AdoptedAmendment` and `LegalUnit` nodes. The draft's single `LawProvision {is_final_law}` with `SURVIVED_AS` straight to the law skips the plenary step, which the reach funnel needs. `TOOK_POSITION` stays `Organisation → Issue`, because stances are coded per organisation per issue, not per chunk per article.
- **Name mapping (draft → canonical):** `Organization` → `Organisation`, `Feedback` → `Comment`, `PoliticalGroup` → `Group`, `LawProvision` → `Provision` (proposal) / `LegalUnit` (final act), `TARGETS` → `AMENDS`, `org_id` = TR number → `org_id` / `tr_id`, `lobbying_costs_eur` → `lobbying_cost_eur`. Update the draft's Cypher examples to these names, or treat the draft as superseded.

### Nodes

| Label | Key (unique constraint) | Properties | Loaded in |
|-------|-------------------------|------------|-----------|
| `Organisation` | `org_id` (= `tr_id` when matched, else `hys:<normalised name>`) | name, user_type, country, company_size, tr_id, lobbying_cost_eur, costs_is_range, fte, interest_represented, head_office, ep_passes_now, ep_passes_all, ec_meetings_all, ec_meetings_vdl1, mep_meetings_all, reg_date, register_category, fields_of_interest, lobbyfacts_url, match_method, match_score | S01 + S06 |
| `Comment` | `feedback_id` | pub_id (13340/14488), track (A/B), date, language, n_words, has_attachment, url | S01 |
| `Chunk` | `chunk_id` | text, idx, n_words, cited_articles, embedding (optional, vector index) | S01 (+ S05) |
| `Provision` | `unit_id` (`Art5(1)(d)`, `Rec27`, `AnnexIII(1)(5)(b)`) | kind, article, path, text (Commission proposal) | S02 / S03 |
| `Amendment` | `am_id` (`PE731.563-123`) | committee, date, location, old_text, new_text, added_text, is_new, deletion, survival_score, survived, reached_law | S03 |
| `AdoptedAmendment` | `adopted_id` (`A9-0188/2023-n`) | text, added_text, date | S04 |
| `LegalUnit` | `law_unit_id` (`2024/1689:Art5(1)(h)`) | kind, article, path, text, consolidated_version | S02 |
| `MEP` | `mep_id` (ParlTrack UserID = Integrity Watch `epid`) | name, country | S03 (+ S06b for MEPs who only appear in meetings) |
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
| `TABLED` | `(MEP)-[:TABLED]->(Amendment)` | weight = 1/n co-signers, is_lead_author | S03 |
| **`MET_WITH`** | `(Organisation)-[:MET_WITH]->(MEP)` | date, title, location, mep_role, ai_related (bool), period (`2020`/`2024`), match_method, match_confidence | S06b |
| `MEMBER_OF` | `(MEP)-[:MEMBER_OF]->(Group)` | from, to | S03 |
| `IN_COMMITTEE` | `(Amendment)-[:IN_COMMITTEE]->(Committee)` | | S03 |
| `SURVIVED_AS` | `(Amendment)-[:SURVIVED_AS]->(AdoptedAmendment)` | score | S04 |
| `BECAME` | `(Provision｜AdoptedAmendment)-[:BECAME]->(LegalUnit)` | score | S04 |
| **`ECHOED_IN`** | `(Chunk)-[:ECHOED_IN]->(Provision)` (Track A) or `->(Amendment)` (Track B) | score, s_embed, s_tfidf, s_ngram, longest_run, verbatim, llm_relation, llm_direction, overlap_quote, tier, carrier_met | S07–S09 (above threshold only); carrier_met after S06b |
| `TOOK_POSITION` | `(Organisation)-[:TOOK_POSITION]->(Issue)` | stance, quote, aligned (bool) | S09 |
| `SIMILAR_TO` | `(Chunk)-[:SIMILAR_TO]->(Chunk)` | score (near-duplicates across orgs → coalitions) | S07 |

**Evidence tiers on `ECHOED_IN`** (text evidence only):
- **T1:** verbatim (≥12 identical words after the stoplist) **and** LLM says `implements`.
- **T2:** verbatim, or LLM says `implements` or `partial`.
- **T3:** semantic score above threshold only.

Separately, an echo also has **reach**: does the path continue to `AdoptedAmendment` and to `LegalUnit`?

And, now that meetings exist, an echo has **access**: did the organisation have a `MET_WITH` edge to an MEP who tabled the echoing amendment? Store it as `ECHOED_IN.carrier_met` (`2020`, `2024`, `both` or null). Access is reported **next to** the tier, never folded into it, because the meetings data has no rows for 2021–mid-2024 and a missing meeting means "not recorded", not "did not meet".

---

## 2. Repo layout and conventions

```
hackathonReversa/
  WebScraping/                   # existing: download_all_feedback.py → parse_and_clean_feedback.py → feedback/*/parsed_*.json
  amendments/                    # existing: ParlTrack dumps, dashboard, exploration notebook
  data_sources/                  # committed: sources.yaml; lobbyfacts/ (register export), integritywatch/ (MEP meetings + register), eurlex/raw (CELLAR HTML), *_units.csv written by S02
  docs/                          # this plan; neo4j_graph_schema.md (draft schema, reconciled in §1)
  neo4j/
    docker-compose.yml           # neo4j:5 + APOC plugins, ports 7474/7687, volume ./neo4j/data (gitignored)
    .env.example                 # sample credentials for local Neo4j container
    schema.cypher                # constraints (7 unique) + indexes (10+ lookup & performance)
    apply_schema.py              # script to execute schema.cypher against Neo4j
    load_data.py                 # loader script: stages 1 & 2 feedback, chunks, LobbyFacts enrichment & deduplication
    load_parltrack.py            # loader script: PoliticalGroups, MEPs, Amendments, LawProvisions, TABLED, TARGETS
    load_meetings.py             # loader script: Integrity Watch MEP meetings -> MET_WITH
    load_all.py                  # master execution script running all loaders in order
    queries/                     # named analysis queries (*.cypher)
      metrics.cypher             # baseline graph counts and exploratory metrics queries
  pipeline/
    config.py                    # paths, PROCEDURE, PUB_IDS, thresholds, model names
    llm.py                       # OpenRouter client (openai SDK, base_url openrouter.ai/api/v1): JSON-schema calls, retries, sqlite cache, cost log
    sources.py                   # load(name) with placeholder handling
    graph.py                     # Neo4j driver wrapper: merge_nodes(label, key, rows), merge_rels(...), batched UNWIND
    s00_check.py                 # sanity check (sources, counts, Neo4j reachable)
    s01_feedback.py              # → Organisation, Comment, Chunk, SUBMITTED, PART_OF, CITES
    s02_legal_texts.py           # EUR-Lex HTML → Provision text, LegalUnit
    s03_amendments.py            # ParlTrack → Amendment, MEP, Group, Committee, AMENDS, TABLED, MEMBER_OF
    s04_survival.py              # plenary + final act → AdoptedAmendment, SURVIVED_AS, BECAME
    s05_features.py              # reads Chunk/target text from Neo4j; stoplist, TF-IDF, embeddings (cached .npy)
    s06_orgs.py                  # LobbyFacts + Integrity Watch register → Organisation properties, merge duplicates
    s06b_meetings.py             # Integrity Watch MEP meetings → MET_WITH (name matching on the lobbyists field)
    s07_match.py                 # candidates + scores → candidate table (parquet)
    s08_calibrate.py             # null distribution → thresholds.json; writes ECHOED_IN above threshold
    s09_judge_and_stance.py      # LLM judge → ECHOED_IN props + tiers; Track A stance → TOOK_POSITION
    s10_analyse.py               # runs neo4j/queries/*.cypher + GDS → data/results/*.csv
    s11_export_dashboard.py      # graph.json + results → dashboard HTML
    s12_eval.py                  # precision on eval/labels.csv
    run.py                       # python -m pipeline.run --from s03 --track B
    serve.py                     # local proxy for the live demo: holds OPENROUTER_API_KEY, streams judge / ask-the-graph calls
  data/                          # gitignored: parquet caches, embeddings, exports
  eval/labels.csv
  .env                           # gitignored: NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD, OPENROUTER_API_KEY, LLM_MODEL, LLM_MODEL_SECOND
```

**Neo4j setup.** Use Docker with `neo4j:5`, `NEO4J_PLUGINS='["apoc","graph-data-science"]'`, and auth from `.env`. Neo4j Desktop works too. Neo4j Aura Free works for sharing, but it has no GDS, so community detection would run in Python there.

**Conventions**
- Every load is idempotent. Use `MERGE` on the unique key and batched `UNWIND $rows` of 1,000. Re-running a stage updates the graph instead of duplicating it.
- `schema.cypher` creates a uniqueness constraint per node key before any load.
- Each stage stamps the nodes and relationships it creates with `source` and `run_id`, so a stage can be wiped and reloaded (`MATCH ()-[r:ECHOED_IN {run_id:$old}]->() DELETE r`).
- Heavy numeric work happens outside Neo4j: embeddings, the n-gram index and TF-IDF are cached in `data/`. Only results go into the graph.
- Requirements: `neo4j` (driver), `pandas`, `pyarrow`, `scikit-learn`, `sentence-transformers`, `rapidfuzz`, `langdetect`, `openai` (Python client, pointed at OpenRouter), `tenacity` (retries), `pyyaml`, plus the PDF library `WebScraping/` already uses.

---

## 3. Stages

### Layer 1: backbone graph (deterministic loads)

**S00 Sanity check**
- Print every source in `sources.yaml` as available or PLACEHOLDER, with row counts and a schema check.
- Feedback files: counts per user_type (COMPANY, BUSINESS_ASSOCIATION, NGO, TRADE_UNION, CONSUMER_ORGANISATION), no duplicate IDs, fewer than 10% empty texts.
- ParlTrack: 4,852 committee and 771 report amendments for the procedure.
- Neo4j is reachable, the plugins are present, and `schema.cypher` is applied.
- Integrity Watch: 6,123 meetings with parseable dates, register IDs unique; warn if the meeting file gains rows outside 2020 / 2024 (coverage changed, update the labels).
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
5. **Add the Integrity Watch register** on the same `tr_id`: `reg_date`, `register_category` (`Cat`), `fields_of_interest` (`FoI`), `mep_meetings_all` (`MepMeetingsNum`) and `ec_meetings_vdl1` (`MeetingsNumVonderleyen1`, 2019–2024). It holds 406 IDs that LobbyFacts lacks, so use it to fill money properties for orgs missing from LobbyFacts (step 2), and set `costs_is_range` when `Costs` is a band.
6. **Set properties:** `lobbying_cost_eur, costs_is_range, fte, interest_represented, ep_passes_now, ep_passes_all, ec_meetings_all, ec_meetings_vdl1, mep_meetings_all, reg_date, lobbyfacts_url, in_register_now, match_method, match_confidence`.

**How the analysis handles unchecked matches**
- Report coverage by user_type and by method in the data-coverage box, e.g. "register number 210 · name match n · unmatched n".
- Money-vs-influence uses matched orgs only. **Run a sensitivity check:** repeat it using only register-number matches (confidence 1.0). If the result holds, name matching isn't driving it.
- Echo, reach and graph analyses don't depend on LobbyFacts. They run on every organisation, matched or not.
- Never impute zero spend for unmatched orgs. Leave it null.

**S06b MEP meetings (Integrity Watch) → `MET_WITH`, fully automatic**
1. Load `mep_meetings.csv` (`sep=';'`, `encoding='utf-8-sig'`, dates `%d/%m/%y`). Join `epid` to `MEP.mep_id` directly; create `MEP` nodes for meeting-only MEPs so the edges aren't lost.
2. Split `lobbyists` cells that list several organisations (on `;`, ` / `, ` and `), then resolve each name to an `Organisation` with the S06 name rules (3a exact normalised name, 3b acronym, 3c fuzzy ≥95 with a clear winner), matching against both the register `Name` and the Have Your Say name. Unmatched names (embassies, ministries, firms not in the consultation) are dropped, not created as nodes.
3. Only Have Your Say organisations matter for the question, so match against those first (about 170 with register IDs, plus name-only ones). Report coverage as "orgs with ≥1 matched meeting / all orgs".
4. Set `ai_related = true` when the title matches AI terms (`artificial intelligence`, `\bAI\b`, `AI-frågor`…) and not "civil liability"; keep the rest as general access.
5. Set `period` from the date and `mep_role` from `role`. Then set `ECHOED_IN.carrier_met` (see §1) with one Cypher pass after S08.

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

**LLM setup (High-Speed Cloud API: Groq / OpenRouter).** Every LLM step (judge, stance coding, proposal coding, and live demo streaming) calls an OpenAI-compatible hosted API with sub-second latency:
- **Groq API** (`https://api.groq.com/openai/v1`): Ultra-fast inference (300–500 tokens/sec with `llama-3.3-70b-versatile`), ideal for instant live judging in the demo and high-throughput batching.
- **OpenRouter API** (`https://openrouter.ai/api/v1`): Flexible multi-provider access with models like `anthropic/claude-haiku-4.5`, `deepseek/deepseek-chat`, or `openai/gpt-4o`.
- **Client implementation:** Standard `openai` Python client configured via `.env` (`LLM_PROVIDER=groq|openrouter`, `GROQ_API_KEY`, `OPENROUTER_API_KEY`). Wrapped in `pipeline/llm.py: complete(prompt, schema, model, stream=False)`.
- **Structured output & caching:** Enforces strict JSON Schema for verdicts (`implements`, `partial`, `contradicts`, `unrelated`), direction (`strengthens_safeguards`, `weakens_obligations`), and quotes. Responses are cached by prompt hash in `data/llm_cache.sqlite` to guarantee zero redundant API cost.
- **Local Live Streaming Proxy (`pipeline/serve.py`):** A lightweight 40-line Python service exposing `/api/judge-stream` to the dashboard frontend, streaming real-time judge tokens from Groq/OpenRouter without exposing API keys to the browser.

**S09 LLM judge and stance coding**
- **Judge (both tracks):**
  - Input: the `ECHOED_IN` pairs, with the org name hidden.
  - `LLM_MODEL` through OpenRouter with a JSON schema, `temperature 0`, cached by prompt hash. Run it as a concurrent batch, capped at about 2,000 pairs per track.
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
   - Compare influence score with `lobbying_cost_eur`, `fte`, `ep_passes_all`, `ec_meetings_vdl1` and `mep_meetings_all`.
   - Use Spearman correlation with a confidence interval, split by user_type. Label it "2026 register figures".
8. **Track A alignment:** the share of an org's `TOOK_POSITION` stances matching `proposal_choice`, by user_type, compared with a random-stance baseline.
9. **Access vs echo (new, Integrity Watch):**
   - For Track B echoes, the share whose carrying MEP had a recorded `MET_WITH` with the organisation, compared with a baseline: the same org paired with random AI Act tabling MEPs from the 55 who appear in the meetings data.
   - Which MEPs and roles (rapporteur, shadow) met the most consultation respondents, and in which group.
   - Track A context: 2020 AI-related meetings of consultation respondents, around the inception consultation (Jul–Sep 2020).
   - Always label it "meetings recorded in 2020 and Jun–Oct 2024 only".

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
- **Live demo** (Neo4j and the dashboard run locally; the LLM features need internet for OpenRouter):
  1. **Neo4j Browser or Bloom** with saved perspectives (organisation → amendment → MEP → law), plus 3–4 prepared Cypher queries from `neo4j/queries/`.
  2. **"Judge this pair live":** a button in the pair viewer sends the lobby chunk and the amendment to the judge model and streams the verdict and overlapping quote in real time.
     - The page calls a tiny local proxy, `pipeline/serve.py` (e.g. `http://localhost:8765/judge`), which holds the API key and forwards the request to OpenRouter with `stream: true`. The key is never in the page.
     - If the call fails, the button falls back to the cached verdict.
  3. **Optional "Ask the graph":** a question in plain language goes to the LLM, which writes Cypher from the schema, runs it on Neo4j, and shows the query and the result. It goes through the same proxy. Text-to-Cypher is more reliable with a hosted model, but still keep 3 rehearsed questions and their prepared queries as a fallback.
  - **Before the demo:** run all batches and warm the cache, check the OpenRouter key has credit, and test the proxy on the venue Wi-Fi. If the network fails, the buttons show cached verdicts.
- **Offline dashboard** (one self-contained HTML file, extending `amendments/build_dashboard.py`). It's built from the `data/results/*.csv` files and `graph.json`, an export of the T1/T2 subgraph.
  - KPI tiles and the reach funnel.
  - Network view (sigma.js), filterable by track, user_type, article, tier and reached-law.
  - Sankey: user_type → org (top 20) → article → survived / reached law.
  - Pair viewer: the lobby chunk next to the amendment, shared phrases highlighted, the LLM verdict and quote.
  - Organisation profile card (LobbyFacts and Integrity Watch figures, echoes by tier, recorded MEP meetings), MEP carrier view with `MET_WITH` edges, money-vs-influence scatter, and the Track A issue heatmap.
  - Method and data-coverage box.

**S12 Evaluation**
Automatic, with no hand-labelling required:
- **Cross-model agreement:** `LLM_MODEL_SECOND` (a different model family) re-judges a stratified sample of 200 `ECHOED_IN` pairs per track (top, near-threshold, random). Report Cohen's κ between the two models. Pairs where they disagree are downgraded one tier.
- **Null check:** the share of null pairs (unrelated DSA amendments) that the judge labels `implements`. This is the judge's false-positive rate, and it should be near 0.
- **Verbatim sanity:** every verbatim pair should be judged `implements` or `partial`. Report the rate.
- **Stability:** for stance coding, report agreement across the 3 seeded runs.
- Optional, if someone has 20 minutes: label 30 pairs in `eval/labels.csv` for a human precision figure.

---

## 4. Order of work and split (about 10 h, 3 people)

| Phase | Time | Person A (graph and data) | Person B (matching) | Person C (analysis and story) |
|------|------|---------------------------|---------------------|-------------------------------|
| 0 | 0:30 | Neo4j via docker-compose, `schema.cypher`, `graph.py` | `pipeline/` skeleton, config, S00; `pipeline/llm.py` OpenRouter client + key in `.env`, smoke-test one judge call | Sketch the views, write the demo story |
| 1 | 2:00 | S01 + S03 loaders → backbone graph | S05 features (stoplist, TF-IDF, embeddings) | S09 codebook, code `proposal_choice` |
| 2 | 3:00 | S02 EUR-Lex units, S04 survival, S06 automatic LobbyFacts + Integrity Watch register join, S06b meetings → `MET_WITH` | S07 matching, S08 calibration → `ECHOED_IN` | First Cypher queries on the backbone |
| 3 | 2:00 | Track B end-to-end on the graph, `carrier_met` pass | S09 judge + stance (OpenRouter, concurrent batch), S12 automatic checks | S10 GDS analyses incl. access vs echo, results CSVs |
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
| Meeting data has a gap (none for 2021–mid-2024) and no dossier field | Use `MET_WITH` as an access signal next to the tier, never as proof; baseline against random tabling MEPs; label every view with the covered periods |
| Meeting lobbyist names have no register ID | Same strict automatic name rules as S06; drop unmatched names; report match coverage |
| Two schema documents drift apart | §1 is canonical; align or retire `docs/neo4j_graph_schema.md` |
| Compromise AMs blur survival | Keep `survival_score`, report partial survival |
| Similarity ≠ causation | Call matches "echoes", show the null baseline, the base rate and the timeline |
| API rate limits, outages or cost overrun | Concurrency 8–16 with backoff on 429/5xx, prompt-hash cache, credit limit on the key, cost logged per run, cached verdicts as demo fallback |
| API key leaks | Key only in gitignored `.env` and the local proxy; never in HTML, notebooks or commits |
| LLM judge errors | JSON schema, temperature 0, the judge only refines already-thresholded pairs, cross-family agreement, null false-positive rate reported |
| Memory pressure (16 GB) | Neo4j heap 2 GB (`NEO4J_server_memory_heap_max__size=2G`); embeddings computed once and cached |

## 6. Definition of done
- `docker compose up` plus `python -m pipeline.run --track A` and `--track B` rebuild the graph from clean in under 30 minutes, excluding the OpenRouter LLM batch (minutes with concurrency, cached after the first run).
- `neo4j/queries/` holds the named analyses, and `data/results/` holds the CSVs, `graph.json` and the dashboard HTML.
- Precision at threshold is reported. The README explains the method, data coverage and caveats on one page.

---

## 7. Implementation Log & Problem Resolution Record

This section serves as a persistent record of technical challenges encountered during implementation and the concrete architectural solutions adopted.

### 1. Scraping Pipeline Reset: Missing NGOs & Consumer Organisations
- **Context & Issue:** The initial feedback collection for Stage 1 (13340) used a filter assuming the European Commission's Better Regulation API labelled non-governmental entities as `NON_GOVERNMENTAL_ORGANIZATION`. In reality, the EC API returns `NGO`, and additionally tags consumer protection bodies as `CONSUMER_ORGANISATION` (e.g., BEUC, Access Now, EDRi). Consequently, civil society responses were erroneously excluded.
- **Resolution:**
  - Redesigned and unified the scraping pipeline into `WebScraping/download_all_feedback.py` and `WebScraping/parse_and_clean_feedback.py`.
  - Downloaded and processed both **Stage 1 (13340)** and **Stage 2 (14488)** in parallel subdirectories (`WebScraping/feedback/stage1_inception_13340/` and `WebScraping/feedback/stage2_proposal_14488/`).
  - Added robust sanitisation (`sanitize_extracted_text`) removing boilerplate headers, decorative punctuation chains, non-printable control characters, and line-split hyphenations, while breaking text into clean paragraph chunks.

### 2. Git Strategy vs. Large Neo4j Binary Files (>100MB)
- **Context & Issue:** When running Neo4j in Docker with a mounted data volume (`neo4j/data/`), Neo4j writes database store files and internal transaction logs (e.g., `neostore.transaction.db.*`). One of these files quickly reached 256 MB. Pushing such files to GitHub fails because GitHub strictly rejects files larger than 100 MB. Furthermore, raw PDF attachments in `attachments/` threatened to bloat repository clone times.
- **Resolution:**
  - Consolidated and cleaned the root `.gitignore`.
  - Added global ignore rules for `neo4j_data/`, `neo4j/data/`, and all attachment directories (`**/attachments/`), while preserving project documentation and scripts.
  - **Collaborative Sharing Model:** Instead of committing binary database snapshots, the team shares:
    1. Schema definition (`neo4j/schema.cypher` & `neo4j/apply_schema.py`).
    2. Version-controlled structured inputs (`WebScraping/feedback/*/parsed_*.json` and `data_sources/lobbyfacts/lobbyfacts_result.csv`).
    3. Deterministic, idempotent loader scripts (`neo4j/load_data.py`).
    Any collaborator can bring up a fresh, identical Neo4j graph in ~1 minute by running `docker compose up -d` followed by `python neo4j/apply_schema.py && python neo4j/load_data.py`.

### 3. Neo4j Unique Constraint Violation during Entity Resolution
- **Context & Issue:**
  - The schema enforces a uniqueness constraint on `:Organisation(org_id)`.
  - During feedback ingestion (S01), organisations without a clean register ID are created with a provisional key (`org_id: "hys:<normalised_name>"`).
  - During LobbyFacts enrichment (S06), when multiple distinct feedback entries (e.g., "Google", "Google Belgium", or multiple submissions with slight spelling variations) were mapped to the same official EU Transparency Register ID (e.g., `0399192837-88`), directly setting `o.org_id = tr_id` caused Neo4j to crash with a `ConstraintValidationFailed` exception (`Node(X) already exists with label Organisation and property org_id = ...`).
- **Resolution:**
  - In `neo4j/load_data.py`, replaced naive property mutation with an atomic **deduplication and merge Cypher query**:
    ```cypher
    MATCH (org:Organisation {org_id: $old_id})
    MERGE (official:Organisation {org_id: $tr_id})
    ON CREATE SET official += properties(org),
                  official.org_id = $tr_id,
                  official.tr_id = $tr_id,
                  official.name = $official_name,
                  official.lobbying_cost_eur = $lobbying_cost_eur,
                  official.fte = $fte,
                  official.ep_passes_all = $ep_passes_all
    ON MATCH SET official.lobbying_cost_eur = coalesce($lobbying_cost_eur, official.lobbying_cost_eur),
                 official.fte = coalesce($fte, official.fte),
                 official.ep_passes_all = coalesce($ep_passes_all, official.ep_passes_all)
    WITH org, official
    WHERE org <> official
    MATCH (org)-[r:SUBMITTED]->(c:Comment)
    MERGE (official)-[:SUBMITTED {date: r.date}]->(c)
    DELETE r, org
    ```
  - This ensures that if an official node already exists, the provisional node transfers all scalar properties and reconnects all incoming/outgoing `[:SUBMITTED]` relationships before being cleanly deleted, perfectly preserving graph integrity without constraint violations.

### 4. Fuzzy Matching with RapidFuzz for Unregistered Feedback
- **Context & Issue:** Many organisations submit consultation comments without entering a valid `trNumber`. Simple string matching fails due to legal suffixes (`e.V.`, `AISBL`, `GmbH`, `Inc.`), capitalization, or minor typos.
- **Resolution:**
  - Leveraged `rapidfuzz.process.extractOne` with `fuzz.token_set_ratio` against the 17,491 LobbyFacts register entries.
  - Set a conservative threshold of 90% to avoid false-positive joins.
  - Successfully matched and enriched 161 unique organisations (including both direct register ID matches and high-confidence fuzzy matches).

### 5. Verified Live Graph Metrics (as of 2026-10-03)
- **Nodes:**
  - `Organization`: 290 unique nodes (161 enriched with LobbyFacts expenditure, FTE, and EP passes)
  - `Feedback`: 335 nodes (98 from Stage 1, 237 from Stage 2)
  - `Chunk`: 2,618 nodes
  - `PoliticalGroup`: 8 nodes (EPP, S&D, Renew, Greens/EFA, The Left, ID, ECR, NI)
  - `MEP`: 171 nodes (all MEPs who tabled amendments on procedure 2021/0106(COD))
  - `Amendment`: 4,852 nodes (all committee amendments on the AI Act)
  - `LawProvision`: 327 nodes (targeted legal units)
- **Relationships:**
  - `[:SUBMITTED]`: 335 (Organization → Feedback)
  - `[:PART_OF]`: 2,618 (Chunk → Feedback)
  - `[:MEMBER_OF]`: 171 (MEP → PoliticalGroup)
  - `[:MET_WITH]`: 147 (Organization → MEP, Integrity Watch meetings)
  - `[:TABLED]`: 19,059 (MEP → Amendment, with lead authorship & co-sign weight)
  - `[:TARGETS]`: 4,852 (Amendment → LawProvision)
- **Top Ingested Spenders Identified in Graph:**
  - Google: €8,000,000 / year (5.5 FTE, 8 EP passes)
  - Apple Inc.: €7,000,000 / year (4.5 FTE, 5 EP passes)
  - Microsoft Corporation: €7,000,000 / year (8.0 FTE, 4 EP passes)
  - Huawei Technologies: €3,500,000 / year (6.3 FTE, 4 EP passes)
  - MedTech Europe: €2,750,000 / year (8.9 FTE, 4 EP passes)

---

## 8. Actionable Roadmap & Agent Execution Steps

This section provides a modular, step-by-step implementation guide specifically designed for an AI agent to execute autonomously and for team members to inspect and validate at each gate.

It directly aligns the remaining work with the judging criteria of **Reversa Challenge 03 · The Influence Atlas** (100 Points):
- **Real links (25 pts):** Side-by-side verification of lobby text vs amendment without legal boilerplate.
- **Any law / Article (20 pts):** Drill down into contested provisions (e.g., Art. 5 Biometrics, Art. 6 High-Risk).
- **Insight (25 pts):** Non-obvious findings (e.g. NGOs vs Big Tech spending efficiency, political group transmission belts).
- **Report & Repo (15 pts):** Open-source, publishable artifact ready for journalists.
- **Ambition (15 pts):** End-to-end trace from consultation submission to EP committee amendment.

```
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                               PIPELINE EXECUTION GATES                                 │
│                                                                                        │
│  [STEP 1A: S07 Match Candidates]                                                      │
│  6-gram Stoplist + Vector Cosine ──► Candidate Echo Pairs (Parquet/Memory)            │
│                                                                                        │
│  [STEP 1B: S09 LLM as a Judge (Groq / OpenRouter)]                                     │
│  Llama-3.3-70b / Claude Haiku ──► Tiers T1/T2, Policy Direction & Overlap Quotes       │
│  Graph Persistence ──► [:ECHOED_IN] edges in Neo4j                                    │
│                                                                                        │
│  [STEP 2: S10 Aggregation & Export]                                                   │
│  Cypher Metrics + Funnel + Carriers ──► data/dashboard_data.json                       │
│                                                                                        │
│  [STEP 3: S11 Frontend Dashboard & Live Proxy]                                        │
│  Dark Glassmorphism SPA + pipeline/serve.py ──► Hero KPIs, Smoking Gun, Scatter,      │
│  Political Matrix, and "Judge this pair live" Streaming Button                         │
│                                                                                        │
│  [STEP 4: Pitch & Live Demo Readiness]                                                │
│  3 Rehearsed Smoking Gun Links + Cypher Verification Queries                          │
└────────────────────────────────────────────────────────────────────────────────────────┘
```

---

### STEP 1A: Vector & Lexical Candidate Matcher (`pipeline/s07_match.py`)
*Goal: Detect candidate influence pairs between lobby Chunks and MEP Amendments while avoiding the legal boilerplate trap (Slide 4).*

1. **Implementation Details:**
   - **Script:** `pipeline/s07_match.py`
   - **Data Input:** Neo4j nodes `Chunk` (2,618 nodes) and `Amendment` (4,852 nodes).
   - **Boilerplate Suppression (The Anti-Trap Filter):**
     - Build a 6-gram stoplist from the original European Commission proposal (`52021PC0206` / `data_sources/eurlex/proposal_units.csv`).
     - Strip all boilerplate n-grams from both chunk texts and amendment `added_text`.
   - **Scoring Pipeline:**
     - Query embeddings from Neo4j (populated by S05) or compute cosine similarity over the embeddings.
     - Select Top candidates exceeding cosine threshold $\ge 0.72$ or targeting matching articles.
     - Compute lexical overlap (longest common token sequence and shared distinctive keywords).
     - Save Top 300–500 candidate pairs to `data/candidates.parquet` for LLM validation.

2. **Execution Command:**
   ```bash
   python pipeline/s07_match.py
   ```

---

### STEP 1B: High-Speed LLM Judge via Groq / OpenRouter (`pipeline/s09_judge.py`)
*Goal: Use a cloud model (Groq Llama 3.3 70B or OpenRouter Claude Haiku) to evaluate candidates, assign evidence tiers, and extract overlapping quotes.*

1. **Implementation Details:**
   - **Script:** `pipeline/s09_judge.py`
   - **Provider & Model:** Groq (`llama-3.3-70b-versatile`) or OpenRouter (`anthropic/claude-haiku-4.5`) via standard OpenAI client.
   - **Prompt & Structured JSON Output:**
     - Evaluates whether the amendment *implements*, *partially adopts*, or *contradicts* the lobby's ask.
     - Classifies legislative direction: `strengthens_safeguards` (civil society) vs `weakens_obligations` (industry).
     - Extracts the verbatim overlap quote (`overlap_quote`).
     - Results cached by prompt hash in `data/llm_cache.sqlite`.
   - **Graph Persistence in Neo4j:**
     - Writes `MERGE (c:Chunk)-[r:ECHOED_IN]->(a:Amendment)` relationships with properties:
       `score`, `tier` (`T1` verbatim, `T2` substantive echo, `T3` semantic), `direction`, `overlap_quote`, `llm_relation`.

2. **Execution Command:**
   ```bash
   python pipeline/s09_judge.py
   ```

3. **Validation & Check Gate (For Human / Agent):**
   Run the following Cypher query in Neo4j:
   ```cypher
   MATCH (c:Chunk)-[r:ECHOED_IN]->(a:Amendment)
   RETURN r.tier AS tier, r.direction AS direction, count(r) AS count, avg(r.score) AS avg_score
   ORDER BY tier;
   ```
   *Success Condition:* 100+ validated `ECHOED_IN` relationships created with verified tiers and direction.

---

### STEP 2: Metrics Aggregation & Export Engine (`pipeline/s10_export_dashboard.py`)
*Goal: Query the live Neo4j graph and produce a clean, self-contained `data/dashboard_data.json` powering the frontend without live database latency during the pitch.*

1. **Implementation Details:**
   - **Script:** `pipeline/s10_export_dashboard.py`
   - **Output File:** `data/dashboard_data.json`
   - **Sections to Compute via Cypher:**
     - **`summary` (Hero KPIs):**
       - Total organisations tracked (290).
       - Total amendments analyzed (4,852).
       - Total echo pairs identified (`ECHOED_IN` count).
       - Conversion funnel by user category: Chunks $\to$ Echoes $\to$ Survived Amendments $\to$ Final Law for `BUSINESS_ASSOCIATION`/`COMPANY` vs `NGO`.
     - **`smoking_gun_pairs` (Top 50 Side-by-Side Candidates for Slide 9's 25 pts):**
       - Array of top-scoring pairs:
         ```json
         {
           "pair_id": "pair_001",
           "org_name": "Google",
           "user_type": "COMPANY",
           "lobby_spend_eur": 8000000,
           "chunk_text": "...",
           "mep_name": "Axel Voss",
           "political_group": "EPP",
           "amendment_id": "PE731.563-120",
           "amendment_text": "...",
           "article": "Art. 5",
           "similarity_score": 0.88,
           "tier": "T1",
           "direction": "weakens_obligations",
           "overlap_snippet": "...",
           "carrier_met": true
         }
         ```
     - **`money_vs_influence` (Data for Scatter Plot - Slide 8 "Who Wins"):**
       - Array of organisations with:
         `name`, `user_type`, `lobbying_cost_eur`, `fte`, `ep_passes_all`, `influence_score` (sum of echo scores weighted by tier), `echoes_count`.
     - **`political_carriers` (Transmission Belts - Slide 5 Q4):**
       - Matrix of Political Group (`EPP`, `S&D`, `Renew`, `Greens/EFA`, etc.) $\times$ Lobby Category (`Corporate`, `NGO`, `Trade Association`), counting amendments sponsored and document meetings (`MET_WITH`).
     - **`battlegrounds` (Contested Provisions - Slide 5 Q2):**
       - Breakdown per Article (e.g., Art. 5 Biometric Surveillance, Art. 6 High-Risk Classification, General Purpose AI) showing clashes between Corporate requests and NGO requests.
     - **`insights_summary` (Journalist-ready Key Takeaways):**
       - 3 to 4 data-backed conclusions answering the 5 core challenge questions.

2. **Execution Command:**
   ```bash
   python pipeline/s10_export_dashboard.py
   ```

3. **Validation & Check Gate (For Human / Agent):**
   ```bash
   python -c "import json; d=json.load(open('data/dashboard_data.json')); print('KPIs:', d['summary']); print('Smoking gun pairs:', len(d['smoking_gun_pairs']))"
   ```
   *Success Condition:* File `data/dashboard_data.json` exists, is valid JSON, and contains all 5 required data arrays with non-null metrics.

---

### STEP 3: Interactive Dashboard ("The Influence Atlas") (`dashboard/index.html` & `dashboard/app.js`)
*Goal: Build an ultra-modern, zero-latency frontend application answering all jury questions with maximum aesthetic and investigative polish.*

1. **Design & Tech Stack:**
   - **Location:** `dashboard/index.html`, `dashboard/app.js`, `dashboard/style.css` (or inline Tailwind CDN).
   - **Aesthetics:** Dark Mode Glassmorphism matching the Reversa Hackathon palette:
     - Background: Deep slate / Charcoal (`#0f172a` / `#0b0f19`)
     - Accents: Toxic/Acid Lime green (`#a3e635` / `#84cc16`), Cyber Blue (`#38bdf8`), Alert Orange (`#fb923c`)
     - Typography: Clean sans-serif (Inter, Outfit or Geist).
   - **Visualization Libraries:** Chart.js or ApexCharts (via CDN, zero bundler setup required so anyone can open `index.html` directly).

2. **The 4 Core Dashboard Views:**
   - **View 1: Hero KPIs & Reach Funnel**
     - Metric counter tiles (Total Orgs, Total Lobby Spend tracked, Amendments influenced, Tobacco/Big Tech vs NGO conversion rate).
     - Funnel visualization showing the drop-off from Lobby Submission $\to$ Echo $\to$ Final Law.
   - **View 2: The "Smoking Gun" Comparison Viewer (Crucial 25-Point Criterium)**
     - Split screen: Left = Lobby Submission (with org budget & badges), Right = MEP Amendment (with author MEP and party badge).
     - Overlapping words highlighted in lime green.
     - Filterable by Article (Art. 5, Art. 6, etc.), by Lobby Category, and by Tier (T1/T2).
     - Badge displaying whether an official meeting was recorded between the MEP and the Lobby (`Integrity Watch Meeting Verified`).
   - **View 3: Money vs. Influence Scatter Plot**
     - X-axis: Annual Lobbying Spend (€, logarithmic scale from €10k to €10M).
     - Y-axis: Cumulative Influence Score.
     - Color code: Corporate (Blue), Trade Associations (Purple), NGOs (Lime).
     - Visual callout on "Outliers": NGOs that punched above their budget weight, and Mega-Spenders with low legislative return.
   - **View 4: Political Carriers & Channels**
     - Heatmap / Stacked Bar: Which political groups carried the most amendments for which lobby sectors.
     - Direct table of the Top 10 "Carrier MEPs" with their top echoed organisations and meeting records.
   - **View 5: Executive Briefing & Forecast (Slide 5 Q5)**
     - Editorial card: *"Who won the AI Act? Who is rising next?"* summarizing the investigative findings for journalists.

3. **Execution Command:**
   ```bash
   # Open directly in browser or serve via python
   python -m http.server 8080 --directory dashboard/
   # Open: http://localhost:8080
   ```

4. **Validation & Check Gate (For Human / Agent):**
   - Verify that all charts render smoothly without console errors.
   - Test clicking on a Smoking Gun pair: both texts should appear side-by-side with highlighting.
   - Test searching or filtering by organization (e.g. "Google", "BEUC").

---

### STEP 4: Pitch & Live Demo Readiness (The 100-Point Jury Runbook)
*Goal: Rehearse and prepare the 5-minute presentation so that the team nails the live checks on stage.*

1. **Live Check Script (Aligned with Slide 9):**
   - **Minute 0-1 (Introduction & Scale):** Show the Hero KPIs in the Dashboard. Present the dataset scale (290 orgs, 4,852 amendments, Integrity Watch meetings).
   - **Minute 1-2 (Real Links Check - 25 Pts):** Open the "Smoking Gun" viewer. Click 3 pre-selected pairs and read the texts side-by-side. Point out the anti-boilerplate badge to prove this is not standard legal wording.
   - **Minute 2-3 (Money vs Influence - 25 Pts):** Show the Scatter Plot. Highlight the non-obvious insight: Big Tech spends millions but civil society won crucial defensive battles on facial recognition.
   - **Minute 3-4 (Channels & Carriers):** Show which MEPs and party groups tabled the amendments and show the verified Integrity Watch meeting links.
   - **Minute 4-5 (The Live Graph Check):** Switch to Neo4j Browser and run the live verification Cypher query:
     ```cypher
     MATCH (o:Organisation)-[:SUBMITTED]->(:Comment)<-[:PART_OF]-(c:Chunk)
           -[e:ECHOED_IN {tier: 'T1'}]->(a:Amendment)<-[:TABLED]-(m:MEP)-[:MEMBER_OF]->(g:Group)
     RETURN o.name, o.lobbying_cost_eur, a.am_id, m.name, g.group_id, e.score
     ORDER BY e.score DESC LIMIT 5;
     ```
   - This proves beyond doubt that the data is backed by an authentic, queryable knowledge graph.

