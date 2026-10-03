#!/usr/bin/env python3
"""Download "Have Your Say" feedback (comments + attached position papers) for one publication.

Usage:
    .venv/bin/python fetch_feedback.py [PUBLICATION_ID] [--out-dir DIR] [--no-attachments] [--max N]

Example (feedback on the AI Act proposal, Apr-Aug 2021):
    .venv/bin/python fetch_feedback.py 14488
"""
import argparse
import csv
import json
import re
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
API = "https://ec.europa.eu/info/law/better-regulation/api"
PAGE_SIZE = 100  # the API caps page size at 100


def get(url, retries=4):
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "amendments-research/1.0"})
            with urllib.request.urlopen(req, timeout=60) as resp:
                return resp.read()
        except Exception as e:  # network hiccups: back off and retry
            if attempt == retries - 1:
                raise
            wait = 2 ** (attempt + 1)
            print(f"  retry in {wait}s ({e})")
            time.sleep(wait)


def fetch_feedback(pub_id):
    items, page = [], 0
    while True:
        data = json.loads(get(f"{API}/allFeedback?publicationId={pub_id}&page={page}&size={PAGE_SIZE}"))
        items.extend(data.get("content", []))
        print(f"  page {page + 1}/{data.get('totalPages', '?')}: {len(items)} comments")
        if data.get("last", True):
            return items
        page += 1


def safe(s, n=60):
    return re.sub(r"[^0-9A-Za-z._-]+", "_", s or "").strip("_")[:n] or "unknown"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("publication_id", nargs="?", default="14488")
    ap.add_argument("--out-dir", type=Path, default=None, help="default: feedback/<publication_id>/")
    ap.add_argument("--no-attachments", action="store_true", help="only download the comments, not the PDFs")
    ap.add_argument("--max", type=int, default=None, help="only keep the first N comments (for testing)")
    args = ap.parse_args()

    out = args.out_dir or HERE / "feedback" / str(args.publication_id)
    pdf_dir = out / "attachments"
    pdf_dir.mkdir(parents=True, exist_ok=True)

    print(f"Fetching feedback for publication {args.publication_id} …")
    items = fetch_feedback(args.publication_id)[: args.max]

    rows = []
    for i, f in enumerate(items, 1):
        files = []
        for a in f.get("attachments") or []:
            doc_id = a.get("documentId")
            if not doc_id:
                continue
            name = f"{f['id']}_{safe(f.get('organization'))}_{safe(Path(a.get('ersFileName') or a.get('fileName') or doc_id).stem)}.pdf"
            path = pdf_dir / name
            if not args.no_attachments and not path.exists():
                print(f"  [{i}/{len(items)}] {name}")
                path.write_bytes(get(f"{API}/download/{doc_id}"))
                time.sleep(0.3)  # be polite to the EC server
            files.append(str(path.relative_to(out)))
        rows.append({
            "id": f.get("id"),
            "date": f.get("dateFeedback"),
            "organization": f.get("organization") or "",
            "user_type": f.get("userType") or "",
            "country": f.get("country") or "",
            "company_size": f.get("companySize") or "",
            "language": f.get("language") or "",
            "name": " ".join(x for x in (f.get("firstName"), f.get("surname")) if x),
            "feedback": f.get("feedback") or "",
            "attachments": "; ".join(files),
        })

    (out / "feedback.json").write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
    with open(out / "feedback.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    n_pdf = sum(1 for _ in pdf_dir.glob("*.pdf"))
    print(f"\nDone: {len(rows)} comments, {n_pdf} attachments in {out}")


if __name__ == "__main__":
    main()
