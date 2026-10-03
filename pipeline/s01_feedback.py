"""S01 Have Your Say feedback -> Organisation, Comment, Chunk, SUBMITTED, PART_OF, CITES.

- Track A = publication 13340 (inception impact assessment), Track B = 14488 (proposal).
- Text is re-chunked into paragraphs of ~80-150 words with a one-sentence overlap (matching targets);
  the parser's ~400-word chunks are kept in data/cache/context_chunks.parquet for LLM context.
- Boilerplate (letterheads, addresses, contact lines, "welcomes the opportunity" openers) is stripped first.
- Organisation key: the Transparency Register number the submitter entered (cleaned), else
  `hys:<normalised name>`. S06 adds register properties and merges duplicates.
- CITES (Comment -> Provision) only for Track B: Track A predates the proposal, so its "Article n"
  references point at other laws. Provision nodes are created as article-level stubs; S02 fills their text.
"""
from __future__ import annotations

import logging
import re
from collections import Counter

import pandas as pd
from langdetect import DetectorFactory, detect

from . import config, graph, sources
from .textutil import clean_tr, n_words, normalise_name, sentences

log = logging.getLogger("s01")
DetectorFactory.seed = 0
SOURCE = "s01_feedback"
INITIATIVE_URL = ("https://ec.europa.eu/info/law/better-regulation/have-your-say/initiatives/"
                  "12527-Artificial-intelligence-ethical-and-legal-requirements/F{id}_en")

# ---------------------------------------------------------------- boilerplate
_BOILER_LINE = re.compile(
    r"""^(
        .{0,60}\b(tel|phone|fax|e-?mail|www\.|https?://)\b.*            # contact lines
      | .{0,80}\b(rue|avenue|street|strasse|straße|square|place|boulevard|chaussée)\b.{0,40}\d{4}.*  # addresses
      | .{0,40}\b\d{4,5}\s+(brussels|bruxelles|brussel|berlin|paris|madrid|rome|vienna|wien|amsterdam)\b.*
      | (transparency\s+register|eu\s+register|register\s+id|id\s*number).{0,60}
      | (page\s+)?\d{1,3}(\s*(/|of)\s*\d{1,3})?                          # page numbers
      | (january|february|march|april|may|june|july|august|september|october|november|december)\s+\d{1,2},?\s+\d{4}
      | \d{1,2}\s+(january|february|march|april|may|june|july|august|september|october|november|december)\s+\d{4}
    )$""",
    re.I | re.X,
)
_BOILER_SENT = re.compile(
    r"(welcomes?|appreciates?|thank(s| you)( you)? for|grateful for).{0,40}(the |this )?"
    r"(opportunity|consultation|invitation)"
    r"|^(yours sincerely|kind regards|best regards|for more information|contact:)",
    re.I,
)
EDGE_SENTENCES = 3  # courtesy openers/closers are only removed from the first/last 3 sentences of a comment


def strip_boilerplate(text: str) -> tuple[list[str], int]:
    """Return cleaned paragraphs and the number of lines/sentences removed."""
    removed = 0
    paras: list[list[str]] = []
    for para in re.split(r"\n\s*\n", text or ""):
        lines = []
        for line in para.splitlines():
            l = line.strip()
            if not l:
                continue
            if len(l) < 200 and _BOILER_LINE.match(l):
                removed += 1
                continue
            lines.append(l)
        p = re.sub(r"\s+", " ", " ".join(lines)).strip()
        if p:
            paras.append(sentences(p))
    total = sum(len(s) for s in paras)
    out, k = [], 0
    for sents in paras:
        keep = []
        for s in sents:
            edge = k < EDGE_SENTENCES or k >= total - EDGE_SENTENCES
            k += 1
            m = _BOILER_SENT.search(s) if edge and len(s.split()) < 60 else None
            if m and m.start() < 120:  # courtesy phrase near the start; a later match means a merged sentence
                removed += 1
            else:
                keep.append(s)
        if keep:
            out.append(" ".join(keep))
    return out, removed


# ---------------------------------------------------------------- chunking
def chunk_paragraphs(paras: list[str], lo: int = config.CHUNK_MIN_WORDS, hi: int = config.CHUNK_MAX_WORDS) -> list[str]:
    """Pack sentences into chunks of lo..hi words, never crossing hi; next chunk repeats the last sentence.
    Paragraph boundaries are preferred break points once a chunk has reached `lo` words."""
    units: list[tuple[str, bool]] = []  # (sentence, ends_paragraph)
    for p in paras:
        ss = sentences(p)
        for i, s in enumerate(ss):
            w = s.split()
            if len(w) > hi:  # very long "sentence" (lists, tables): hard split
                for j in range(0, len(w), hi):
                    units.append((" ".join(w[j : j + hi]), False))
                units[-1] = (units[-1][0], i == len(ss) - 1)
            else:
                units.append((s, i == len(ss) - 1))

    chunks: list[str] = []
    cur: list[str] = []
    cur_n = 0
    for s, end_para in units:
        sn = n_words(s)
        if cur and cur_n + sn > hi:
            chunks.append(" ".join(cur))
            last = cur[-1]
            cur, cur_n = ([last], n_words(last)) if n_words(last) + sn <= hi else ([], 0)
        cur.append(s)
        cur_n += sn
        if end_para and cur_n >= lo:
            chunks.append(" ".join(cur))
            last = cur[-1]
            cur, cur_n = [last], n_words(last)
    # tail: if it's only the overlap sentence, drop it; if short, merge into previous when that fits
    if cur and not (chunks and len(cur) == 1 and chunks[-1].endswith(cur[0])):
        tail = " ".join(cur)
        if chunks and cur_n < lo and n_words(chunks[-1]) + cur_n - n_words(cur[0]) <= hi + 40:
            chunks[-1] = chunks[-1] + " " + " ".join(cur[1:]) if len(cur) > 1 else chunks[-1]
        else:
            chunks.append(tail)
    return [c for c in chunks if c.strip()]


# ---------------------------------------------------------------- citations
_OTHER_LAW = re.compile(
    r"^.{0,45}\b(GDPR|TFEU|TEU|Charter|Directive|2016/679|2019/1020|of Regulation \(EU\) 20(?!21/0106)|"
    r"MDR|IVDR|DSA|DMA|Convention|ECHR|Treaty|LED)\b", re.I)
_ART = re.compile(r"\bArt(?:icle)?s?\.?\s*((?:\d{1,3}(?:\s*\(\d+\))?(?:\s*\([a-z]\))?(?:\s*(?:,|and|&|to|-|–)\s*)?)+)", re.I)
_ANNEX = re.compile(r"\bAnnex(?:es)?\s+((?:[IVX]{1,4}\b(?:\s*(?:,|and|&|-|–)\s*)?)+)")
_REC = re.compile(r"\bRecitals?\s+((?:\d{1,3}(?:\s*(?:,|and|&|-|–)\s*)?)+)", re.I)
ROMAN = {"I": 1, "II": 2, "III": 3, "IV": 4, "V": 5, "VI": 6, "VII": 7, "VIII": 8, "IX": 9}
MAX_ART, MAX_ANNEX, MAX_REC = 85, 9, 89  # COM(2021)206 has 85 articles, 9 annexes, 89 recitals


def cited_units(text: str) -> list[str]:
    out = []
    for m in _ART.finditer(text):
        if _OTHER_LAW.match(text[m.end() : m.end() + 60]):
            continue
        for n in re.findall(r"(?<![(\d])(\d{1,3})(?![\d)])", m.group(1)):
            if 1 <= int(n) <= MAX_ART:
                out.append(f"Art{int(n)}")
    for m in _ANNEX.finditer(text):
        if _OTHER_LAW.match(text[m.end() : m.end() + 60]):
            continue
        for r in re.findall(r"[IVX]{1,4}", m.group(1)):
            if ROMAN.get(r, 99) <= MAX_ANNEX:
                out.append(f"Annex{r}")
    for m in _REC.finditer(text):
        if _OTHER_LAW.match(text[m.end() : m.end() + 60]):
            continue
        for n in re.findall(r"\d{1,3}", m.group(1)):
            if 1 <= int(n) <= MAX_REC:
                out.append(f"Rec{int(n)}")
    return out


def _lang(text: str) -> str | None:
    try:
        return detect(text[:5000])
    except Exception:  # noqa: BLE001
        return None


# ---------------------------------------------------------------- build tables
def build(track: str) -> dict[str, pd.DataFrame]:
    pub = config.PUB_IDS[track]
    recs = sources.load(f"hys_feedback_{pub}")
    raw = {str(r["id"]): r for r in sources.raw_feedback(pub)}
    orgs, comments, chunks, ctx, cites = [], [], [], [], []
    stats = Counter()
    for r in recs:
        fid = str(r["feedback_id"])
        rr = raw.get(fid, {})
        tr = clean_tr(rr.get("trNumber"))
        name = (r.get("organization") or rr.get("organization") or "").strip()
        norm = normalise_name(name)
        org_id = tr or f"hys:{norm}"
        orgs.append(dict(org_id=org_id, name=name, name_norm=norm, user_type=r.get("user_type"),
                         country=r.get("country"), company_size=rr.get("companySize"), tr_id=tr))
        date = pd.to_datetime(rr.get("dateFeedback"), format="%Y/%m/%d %H:%M:%S", errors="coerce")
        paras, removed = strip_boilerplate(r.get("full_text") or "")
        stats["boiler_removed"] += removed
        text = "\n\n".join(paras)
        comments.append(dict(feedback_id=fid, org_id=org_id, pub_id=pub, track=track,
                             date=date.date().isoformat() if pd.notna(date) else None,
                             language=(rr.get("language") or "").lower() or None, text_language=_lang(text),
                             n_words=n_words(text), n_words_raw=r.get("total_words"),
                             has_attachment=bool(r.get("has_attachment")), url=INITIATIVE_URL.format(id=fid)))
        cits = Counter()
        for i, c in enumerate(chunk_paragraphs(paras)):
            cu = cited_units(c)
            cits.update(cu)
            chunks.append(dict(chunk_id=f"{fid}:{i}", feedback_id=fid, idx=i, text=c, n_words=n_words(c),
                               cited_articles=sorted(set(cu)), track=track))
        for i, c in enumerate(r.get("chunks") or []):
            ctx.append(dict(ctx_id=f"{fid}:ctx{i}", feedback_id=fid, idx=i, text=c, track=track))
        if track == "B":
            cites += [dict(start=fid, end=u, count=n) for u, n in cits.items()]
    log.info("track %s: %d comments, %d chunks, %d boilerplate lines/sentences removed",
             track, len(comments), len(chunks), stats["boiler_removed"])
    return {k: pd.DataFrame(v) for k, v in
            dict(orgs=orgs, comments=comments, chunks=chunks, context_chunks=ctx, cites=cites).items()}


def _provision_stub(uid: str) -> dict:
    if uid.startswith("Art"):
        return dict(unit_id=uid, kind="article", article=uid[3:], path=uid)
    if uid.startswith("Annex"):
        return dict(unit_id=uid, kind="annex", article=uid, path=uid)
    return dict(unit_id=uid, kind="recital", article=None, path=uid)


def load_graph(t: dict[str, pd.DataFrame]) -> None:
    graph.apply_schema()
    # one node per org; when an org submitted several times keep the first non-null value per field
    orgs = t["orgs"].groupby("org_id", as_index=False).first()
    graph.merge_nodes("Organisation", "org_id", orgs, SOURCE)
    graph.merge_nodes("Comment", "feedback_id", t["comments"].drop(columns=["org_id"]), SOURCE)
    graph.merge_nodes("Chunk", "chunk_id", t["chunks"].drop(columns=["feedback_id"]), SOURCE)
    sub = t["comments"][["org_id", "feedback_id", "date"]].rename(columns={"org_id": "start", "feedback_id": "end"})
    graph.merge_rels("SUBMITTED", "Organisation", "org_id", "Comment", "feedback_id", sub, SOURCE)
    po = t["chunks"][["chunk_id", "feedback_id", "idx"]].rename(columns={"chunk_id": "start", "feedback_id": "end"})
    graph.merge_rels("PART_OF", "Chunk", "chunk_id", "Comment", "feedback_id", po, SOURCE)
    if len(t["cites"]):
        stubs = pd.DataFrame([_provision_stub(u) for u in t["cites"]["end"].unique()])
        # stubs only set fields that are still empty, so they never overwrite S02 text
        graph.run("""UNWIND $rows AS row MERGE (p:Provision {unit_id: row.unit_id})
                     ON CREATE SET p += row, p.source = $s, p.run_id = $r""",
                  rows=stubs.to_dict("records"), s=SOURCE, r=config.RUN_ID)
        graph.merge_rels("CITES", "Comment", "feedback_id", "Provision", "unit_id", t["cites"], SOURCE)


def main(track: str | None = None) -> None:
    tracks = [track] if track else ["A", "B"]
    for tr in tracks:
        t = build(tr)
        for name, df in t.items():
            df.to_parquet(config.CACHE / f"s01_{name}_{tr}.parquet", index=False)
        load_graph(t)
        print(f"   track {tr}: {len(t['comments'])} comments, {t['orgs'].org_id.nunique()} orgs, "
              f"{len(t['chunks'])} chunks (median {int(t['chunks'].n_words.median())} words), "
              f"{len(t['cites'])} CITES pairs")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    main()
