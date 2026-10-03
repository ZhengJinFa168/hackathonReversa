"""Legal-unit identifiers shared by S01-S04.

unit_id scheme (matches ParlTrack `location` strings):
    Recital 27                                         -> Rec27
    Article 5                                          -> Art5
    Article 5 – paragraph 1 – point d                  -> Art5(1)(d)
    Article 5 – paragraph 1 – point d – point iii      -> Art5(1)(d)(iii)
    Article 3 – paragraph 1 – point 40                 -> Art3(1)(40)
    Annex III – paragraph 1 – point 5 – point b        -> AnnexIII(1)(5)(b)
    Article 52 – paragraph 3 – introductory part       -> Art52(3)
    Article 9 – paragraph 2 a (new)                    -> Art9(2a)   (is_new=True, base unit Art9)
"""
from __future__ import annotations

import re

_SEP = re.compile(r"\s+[–—-]\s+")
_HEAD = re.compile(r"^(Recital|Article|Annex|Citation)\s+([0-9]+|[IVXLC]+)\s*([a-z]{1,2})?\s*(\(new\))?$", re.I)
_PART = re.compile(r"^(paragraph|point|subparagraph|indent)\s+([0-9]+|[a-z]{1,3}|[ivxlc]+)\s*([a-z]{1,2})?\s*(\(new\))?$", re.I)
IGNORE_PARTS = {"introductory part", "title", "subtitle", "heading", "final part", "concluding part"}


def location_to_unit(loc: str) -> dict | None:
    """Parse a ParlTrack location into {unit_id, base_unit_id, is_new, kind}. None if not a legal unit."""
    if not loc:
        return None
    parts = [p.strip() for p in _SEP.split(loc.strip()) if p.strip()]
    m = _HEAD.match(parts[0])
    if not m:
        return None
    kind, num, suffix, new = m.group(1).capitalize(), m.group(2), m.group(3) or "", bool(m.group(4))
    prefix = {"Recital": "Rec", "Article": "Art", "Annex": "Annex", "Citation": "Cit"}[kind]
    uid = base = f"{prefix}{num}"
    base_closed = bool(suffix)  # "Article 61 a (new)": a new article; the existing base is the article before it
    uid = f"{prefix}{num}{suffix}"
    for p in parts[1:]:
        if p.lower() in IGNORE_PARTS:
            continue
        pm = _PART.match(p)
        if not pm:
            break
        lab, sfx, pnew = pm.group(2), pm.group(3) or "", bool(pm.group(4))
        uid += f"({lab}{sfx})"
        if not base_closed:
            if sfx or pnew:
                base_closed = True
            else:
                base = uid
        new = new or pnew
    return {"unit_id": uid, "base_unit_id": base, "is_new": new, "kind": kind.lower()}


def parent(uid: str) -> str | None:
    i = uid.rfind("(")
    return uid[:i] if i > 0 else None


def top(uid: str) -> str:
    i = uid.find("(")
    return uid if i < 0 else uid[:i]


def resolve(uid: str, known) -> tuple[str | None, bool]:
    """Closest existing unit for `uid`: exact, then annex numbering variants (ParlTrack writes both
    'Annex III – paragraph 1 – point 5' and 'Annex VIII – point 11'), then parents.
    Returns (unit_id or None, exact)."""
    cands = [uid]
    if uid.startswith("Annex"):
        head = top(uid)
        rest = uid[len(head):]
        cands.append(head + "(1)" + rest)
        if rest.startswith("(1)"):
            cands.append(head + rest[3:])
    for c in cands:
        if c in known:
            return c, True  # annex numbering variants name the same unit
    for c in cands:
        p = parent(c)
        while p:
            if p in known:
                return p, False
            p = parent(p)
    t = top(uid)
    return (t, False) if t in known else (None, False)
