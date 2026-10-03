"""Text helpers shared by several stages: name normalisation, TR-number cleaning, sentence split, word counts."""
from __future__ import annotations

import re
import unicodedata

TR_RE = re.compile(r"\d{6,15}-\d{2}")

LEGAL_FORMS = {
    "ev", "e v", "aisbl", "asbl", "ivzw", "vzw", "sa", "nv", "bv", "ltd", "limited", "gmbh", "ag", "sarl", "sas",
    "srl", "spa", "s p a", "plc", "inc", "llc", "llp", "corp", "corporation", "co", "kg", "oy", "oyj", "ab", "as",
    "aps", "a s", "se", "sl", "sco", "ry", "cic", "eeig", "gie", "ug", "ggmbh", "ev", "zrt", "kft", "sp z o o",
}


def clean_tr(value) -> str | None:
    """Transparency Register number from free text: strips spaces, prefixes, zero-width chars, trailing dots."""
    if not value:
        return None
    m = TR_RE.search(re.sub(r"[\s​‌‍﻿]", "", str(value)))
    return m.group(0) if m else None


def ascii_fold(s: str) -> str:
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()


def normalise_name(name: str) -> str:
    """lowercase, ASCII, bracketed parts and legal forms removed, punctuation collapsed."""
    s = ascii_fold(name or "").lower()
    s = re.sub(r"\([^)]*\)|\[[^\]]*\]", " ", s)
    s = s.replace("&", " and ")
    s = re.sub(r"[^a-z0-9]+", " ", s).strip()
    toks = s.split()
    while toks and toks[-1] in LEGAL_FORMS:
        toks.pop()
    out = " ".join(toks)
    for lf in sorted((f for f in LEGAL_FORMS if " " in f), key=len, reverse=True):
        if out.endswith(" " + lf):
            out = out[: -len(lf) - 1]
    return out.strip()


def n_words(s: str) -> int:
    return len((s or "").split())


_SENT = re.compile(r"(?<=[.!?;:])\s+(?=[\"'“(\[]?[A-Z0-9•\-–])")


def sentences(paragraph: str) -> list[str]:
    return [s.strip() for s in _SENT.split(paragraph) if s.strip()]
