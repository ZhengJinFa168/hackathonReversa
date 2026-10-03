"""S06b Integrity Watch MEP meetings -> MET_WITH (Organisation -> MEP), fully automatic.

- epid joins MEP.mep_id directly; MEPs that only appear in the meetings get MEP nodes (in_meetings_only = true).
- `lobbyists` is free text with no register ID. Each cell is matched as a whole first, then split on
  ';', ' / ', ' and ', ', ' and each part matched. Register numbers written in the cell match directly (1.0). Names are resolved only against the Have Your Say
  organisations (their own name and their register name), with the S06 rules adapted to a field that has
  no country:
    exact normalised name                                                         -> 0.9
    explicit acronym (bracketed or standalone), >= 3 letters, unique among them  -> 0.8
    token_set_ratio >= 95 with one name starting with the other or token_sort >= 95, clear winner -> 0.7
  Unmatched names (embassies, ministries, firms outside the consultation) are dropped, not created.
- One MET_WITH per meeting (keyed by meeting_id), with date, title, location, mep_role, period (2020 / 2024)
  and ai_related (AI terms in the title, excluding the separate AI civil-liability file).
- Coverage: meetings exist only for 2020 and Jun-Oct 2024. A missing meeting means "not recorded".
"""
from __future__ import annotations

import hashlib
import logging
import re

import pandas as pd
from rapidfuzz import fuzz, process

from . import config, graph, sources
from .s06_orgs import _acronym, mnorm
from .textutil import TR_RE

log = logging.getLogger("s06b")
SOURCE = "s06b_meetings"
AI_RE = re.compile(r"artificial intelligence|intelligence artificielle|k(ü|ue)nstliche intelligenz|inteligencia artificial|"
                   r"intelligenza artificiale|sztuczn\w* inteligencj\w*|\bAI\b|\bA\.I\.|\bIA\b|\bKI\b|\bAI-|\bAIA\b", re.I)
NOT_AI_RE = re.compile(r"liabilit|haftung|responsabilit|aansprakelijk", re.I)  # AI civil liability is another file
SPLIT_RE = re.compile(r"\s*;\s*|\s+/\s+|\s+and\s+|,\s+(?=[A-Z])")


def _alnum(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


class OrgIndex:
    def __init__(self, orgs: pd.DataFrame):
        self.names: dict[str, str] = {}       # normalised name -> org_id
        self.unsafe: dict[str, set[str]] = {}  # one short token left after stripping brackets -> raw names
        self.full: dict[str, list[str]] = {}  # org_id -> its normalised names
        self.acr: dict[str, set[str]] = {}    # acronym -> org_ids
        self.tr: dict[str, str] = {}          # register number -> org_id
        for r in orgs.itertuples():
            if isinstance(r.tr_id, str):
                self.tr[r.tr_id] = r.org_id
            for n in (r.name, r.register_name):
                if isinstance(n, str) and n.strip():
                    k = mnorm(n)
                    self.names.setdefault(k, r.org_id)
                    self.full.setdefault(r.org_id, []).append(mnorm(re.sub(r"[()]", " ", n)))
                    if " " not in k and len(k) <= 6 and "(" in n:
                        # "ACEA (European Automobile ...)" normalises to "acea", which also names Acea S.p.A.
                        self.unsafe.setdefault(k, set()).add(_alnum(n))
                    for a in _acronym(n, initials=False) | {a for a in _acronym(n) if a.isalpha() and len(a) >= 4
                                                            and a == n.split()[0].upper().strip("()-")}:
                        self.acr.setdefault(a, set()).add(r.org_id)
        self.keys = [k for k in self.names if k]

    def match(self, s: str, fuzzy: bool = True) -> tuple[str | None, str, float]:
        n = mnorm(s)
        if not n:
            return None, "none", 0.0
        if n in self.names and (n not in self.unsafe or _alnum(s) in self.unsafe[n]):
            return self.names[n], "exact_name", 0.9
        # explicit acronym, unique; when the string also spells out a name, that name must fit the organisation
        acr = _acronym(s, initials=False)
        hits = {o for a in acr for o in self.acr.get(a, set())}
        if len(hits) == 1:
            o = next(iter(hits))
            rest = mnorm(re.sub(r"\b(" + "|".join(map(re.escape, acr)) + r")\b|[()]", " ", s))
            bare = len(rest.split()) < 2
            # a bare 3-letter acronym is ambiguous without a country ("EDF": Disability Forum or the utility)
            if (bare and max(len(a) for a in acr) >= 4) or (
                    not bare and max(fuzz.token_set_ratio(rest, f) for f in self.full[o]) >= 70):
                return o, "acronym", 0.8
        if not fuzzy:
            return None, "none", 0.0
        # fuzzy: subset matches only when one name starts with the other, and the shorter one is not a
        # bare short word ("Paris" -> "Paris EUROPLACE")
        res = process.extract(n, self.keys, scorer=fuzz.token_set_ratio, limit=2)
        if not res:
            return None, "none", 0.0
        (best, score, _), second = res[0], (res[1][1] if len(res) > 1 else 0)
        shorter = min(n, best, key=len)
        aligned = fuzz.token_sort_ratio(n, best) >= 95 or (
            (best.startswith(n + " ") or n.startswith(best + " ")) and (" " in shorter or len(shorter) >= 6))
        if score >= 95 and score - second >= 5 and aligned and best not in self.unsafe:
            return self.names[best], "fuzzy", 0.7
        return None, "none", 0.0


def match_cell(idx: OrgIndex, cell: str) -> list[tuple[str, str, float, str]]:
    """All organisations named in one `lobbyists` cell: register numbers first, then the whole cell
    (exact / acronym only), then each part after splitting (fuzzy allowed)."""
    found: dict[str, tuple[str, float, str]] = {}

    def add(o, meth, conf, p):
        if o and conf > found.get(o, ("", 0.0, ""))[1]:
            found[o] = (meth, conf, p)

    for tr in TR_RE.findall(cell):
        add(idx.tr.get(tr), "tr_number", 1.0, tr)
    clean = TR_RE.sub(" ", cell).strip(" ,;")
    o, meth, conf = idx.match(clean, fuzzy=False)
    if o:
        add(o, meth, conf, clean)
    else:
        for p in SPLIT_RE.split(clean):
            if p.strip(" ,;()"):
                add(*idx.match(p.strip(" ,;")), p.strip(" ,;"))
    return [(o, m, c, p) for o, (m, c, p) in found.items()]


def build() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    m = sources.load("integritywatch_meetings").reset_index(drop=True)
    orgs = pd.DataFrame(graph.run("MATCH (o:Organisation) RETURN o.org_id AS org_id, o.name AS name, "
                                  "o.register_name AS register_name, o.tr_id AS tr_id"))
    idx = OrgIndex(orgs)
    rows, names = [], []
    for r in m.itertuples():
        cell = (r.lobbyists or "").strip()
        hits = match_cell(idx, cell)
        names.append(dict(lobbyists=cell, n_matched=len(hits)))
        title = r.title if isinstance(r.title, str) else ""
        for oid, meth, conf, p in hits:
            mid = hashlib.sha1(f"{r.epid}|{r.date}|{title}|{cell}|{oid}".encode()).hexdigest()[:16]
            rows.append(dict(meeting_id=mid, start=oid, end=int(r.epid), date=r.date.date().isoformat(),
                             period=str(r.date.year), title=title or None, location=r.location,
                             mep_role=r.role, lobbyists=cell, matched_name=p,
                             ai_related=bool(AI_RE.search(title)) and not NOT_AI_RE.search(title),
                             match_method=meth, match_confidence=conf))
    meps = (m.assign(mep_id=m.epid.astype(int))
            .groupby("mep_id", as_index=False)
            .agg(name=("mep", "first"), iw_group=("group", "first"), country=("country", "first")))
    return pd.DataFrame(rows), meps, pd.DataFrame(names)


def load_graph(met: pd.DataFrame, meps: pd.DataFrame) -> None:
    known = {r["id"] for r in graph.run("MATCH (m:MEP) RETURN m.mep_id AS id")}
    new = meps[~meps.mep_id.isin(known)].assign(in_meetings_only=True)
    graph.merge_nodes("MEP", "mep_id", new, SOURCE)
    # every MEP in the meetings file gets its Integrity Watch group label (no overwrite of ParlTrack fields)
    graph.run("UNWIND $rows AS r MATCH (m:MEP {mep_id: r.mep_id}) SET m.iw_group = r.iw_group",
              rows=meps[["mep_id", "iw_group"]].to_dict("records"))
    graph.run("MATCH ()-[r:MET_WITH]->() DELETE r")
    rows = met.to_dict("records")
    for b in range(0, len(rows), 1000):
        graph.run("""
            UNWIND $rows AS row
            MATCH (o:Organisation {org_id: row.start}) MATCH (m:MEP {mep_id: row.end})
            MERGE (o)-[r:MET_WITH {meeting_id: row.meeting_id}]->(m)
            SET r += apoc.map.removeKeys(row, ['start', 'end']), r.source = $s, r.run_id = $run""",
                  rows=rows[b : b + 1000], s=SOURCE, run=config.RUN_ID)


def main(track: str | None = None) -> None:
    met, meps, names = build()
    for k, df in dict(met_with=met, meeting_meps=meps, lobbyist_names=names).items():
        df.to_parquet(config.CACHE / f"s06b_{k}.parquet", index=False)
    load_graph(met, meps)
    n_orgs = graph.run("MATCH (o:Organisation) RETURN count(o) AS n")[0]["n"]
    with_m = met.start.nunique()
    print(f"   {len(met)} MET_WITH edges ({met.ai_related.sum()} AI-related) between {with_m} of {n_orgs} "
          f"consultation organisations and {met.end.nunique()} MEPs; methods {met.match_method.value_counts().to_dict()}")
    print(f"   by period: {met.period.value_counts().to_dict()}; meeting-only MEP nodes added: "
          f"{(~meps.mep_id.isin(graph.run('MATCH (m:MEP) WHERE m.source <> $s RETURN collect(m.mep_id) AS ids', s=SOURCE)[0]['ids'])).sum()}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
