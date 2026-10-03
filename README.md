# hackathonReversa

A data pipeline and dashboard for tracing lobbying influence in EU legislation (the AI Act).

This tool tracks stakeholder comments from the European Commission's "Have Your Say" portal through to the final adopted text, by identifying semantic similarity and verbatim overlap with committee amendments.

## Pipeline Steps
- **S01-S06**: Data extraction (legal texts, amendments, comments, transparency register data, meetings).
- **S07**: Embedding and candidate generation using `multilingual-e5-base`.
- **S08**: Null-calibration against an unrelated act (DSA) to set a significance threshold for similarity.
- **S09**: LLM Judge evaluates the direction and relation (implements, partial, contradicts) of echoes.
- **S10**: Statistical and graph analysis (influence, networks, reach models).
- **S11**: Export to a standalone web dashboard and live demo app.
- **S12**: Evaluation checks (cross-model agreement, false-positive rate on null data, verbatim sanity).

## Dashboard
The exported dashboard (`demo.html`) is a standalone interactive tool that allows exploration of:
- **Lobbies**: Rankings by influence, spend, and access.
- **Articles**: The most relevant comments per article.
- **Stories**: End-to-end trace of a comment influencing the final law.
- **Politicians**: MEP activity and alignments.
- **Methodology**: Details on data coverage and LLM thresholds.

## Evaluation
The pipeline is evaluated using a secondary LLM for cross-model agreement, checking verbatim pairs, and measuring the false positive rate on unrelated legal texts. The results are available in `data/results/eval.csv`.

## Build
To build the demo:
```bash
uv run python -m pipeline.run
uv run python -m pipeline.s11b_demo
```