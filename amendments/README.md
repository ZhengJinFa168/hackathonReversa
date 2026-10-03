# EU Parliament committee amendments → dashboard

Filters ParlTrack's committee-amendments dump for one legislative procedure
(default: the AI Act, `2021/0106(COD)`), saves the subset, and builds a
self-contained HTML dashboard.

## Run

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt   # once
.venv/bin/python build_dashboard.py "2021/0106(COD)"
open "output/dashboard_2021_0106_COD.html"
```

Any procedure reference works, e.g. `.venv/bin/python build_dashboard.py "2020/0361(COD)"`.
Options: `--data-dir` (folder with the dumps, default: this folder), `--out-dir` (default `output/`).

## Inputs (in this folder)

- `ep_amendments.json.zst` (or decompressed `.json`) – committee amendments, streamed line by line
- `ep_meps.json.zst` – used for MEP names and their political group on the tabling date

## Outputs (`output/`)

- `amendments_<ref>.json` – raw ParlTrack records for the procedure
- `amendments_<ref>.csv` – one row per amendment: id, PE doc, date, committee, location,
  signatories, groups, old text, new text, words added, justification, source PDF
- `dashboard_<ref>.html` – charts (by tabling date/committee, by political group, top MEPs,
  per Article / Recital / Annex, most-amended provisions) and a searchable table with a word-level
  diff of Commission text vs amendment (green = added, red = removed)

## Notes

- Each co-signatory is credited for every amendment they signed (MEP and group charts).
- ~100 amendments have no parsed location in ParlTrack and are shown as "Unspecified".
- Plenary amendments (`ep_plenary_amendments.json.zst`) are not included yet.

## Lobby / stakeholder feedback ("Have Your Say")

```bash
.venv/bin/python fetch_feedback.py 14488          # feedback on the AI Act proposal (304 comments, Apr-Aug 2021)
.venv/bin/python fetch_feedback.py 14488 --no-attachments   # comments only, no PDFs
```

Writes `feedback/<publicationId>/feedback.csv`, `feedback.json` and `attachments/*.pdf`.
Other AI Act rounds: `13340` (inception impact assessment, 2020).

## Explore the raw data

```bash
.venv/bin/jupyter lab explore_data.ipynb
```

Prints the first raw records and a field table (presence %, type, example) for every dump,
the AI Act amendments as a DataFrame, and the columns of any generated CSVs.
