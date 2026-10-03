"""S03 ParlTrack committee amendments -> Amendment, MEP, Group, Committee + AMENDS, TABLED, MEMBER_OF, IN_COMMITTEE.

Reuses amendments/build_dashboard.py for the procedure filter, PDF line joining, MEP -> group at the
tabling date and the word diff.
- `added_text` holds only the inserted words (the Track B matching target); a provision the amendment
  creates ("(new)") has its whole text as added_text. Deletions keep old_text and set deletion = true.
- AMENDS points at the proposal unit from S02 (pipeline/units.py): the exact unit when it exists,
  else the closest parent; new provisions point at the unit they are inserted after (`exact = false`).
- TABLED weight = 1 / number of co-signers; the first listed MEP is the lead author.
- mep_id is the ParlTrack UserID as an integer (= Integrity Watch `epid`).
"""
from __future__ import annotations

import importlib.util
import logging

import pandas as pd

from . import config, graph
from .units import location_to_unit, resolve

log = logging.getLogger("s03")
SOURCE = "s03_amendments"
TERM_START, TERM_END = "2019-07-02", "2024-07-15"  # 9th parliamentary term


def _dashboard():
    spec = importlib.util.spec_from_file_location("build_dashboard", config.ROOT / "amendments" / "build_dashboard.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _country_at(mep: dict, date: str) -> str | None:
    for c in mep.get("Constituencies") or []:
        if c and c.get("start", "") <= date <= c.get("end", "9999"):
            return c.get("country")
    cs = [c for c in mep.get("Constituencies") or [] if c]
    return cs[-1].get("country") if cs else None


def build() -> dict[str, pd.DataFrame]:
    bd = _dashboard()
    data_dir = config.ROOT / "amendments"
    recs = bd.load_amendments(data_dir, config.PROCEDURE)
    mep_ids = {i for r in recs for i in (r.get("meps") or [])}
    meps = bd.load_meps(data_dir, mep_ids)
    df = bd.build_frame(recs, meps)
    log.info("%d amendments, %d MEPs (%d found in the MEP dump)", len(df), len(mep_ids), len(meps))

    provisions = set(pd.read_parquet(config.CACHE / "s02_proposal_units.parquet").unit_id)
    am_rows, amends, tabled = [], [], []
    for r in df.itertuples():
        u = location_to_unit(r.location)
        uid = u["unit_id"] if u else None
        target, exact = (resolve(u["base_unit_id"] if u["is_new"] else uid, provisions) if u else (None, False))
        is_new = bool(u and u["is_new"]) or r.is_new
        added = r.new if (is_new and not r.old) else r.added_words
        am_rows.append(dict(
            am_id=r.id, peid=r.pe, seq=str(r.seq), committee=r.committee, date=r.date, location=r.location,
            unit_id=uid, target_unit_id=target, old_text=r.old or None, new_text=r.new or None,
            added_text=added or None, n_added_words=len((added or "").split()), is_new=is_new,
            deletion=bool(r.deletion), authors=r.authors, n_cosigners=len(r.mep_ids), src=r.src,
            justification=r.justification or None))
        if target:
            amends.append(dict(start=r.id, end=target, is_new=is_new, deletion=bool(r.deletion), exact=exact))
        n = len(r.mep_ids)
        for k, (mid, grp) in enumerate(zip(r.mep_ids, r.groups)):
            tabled.append(dict(start=int(mid), end=r.id, weight=1.0 / n, is_lead_author=(k == 0), group=grp))

    mep_rows, member_of = [], []
    for mid in mep_ids:
        m = meps.get(mid, {})
        name = (m.get("Name") or {}).get("full") or str(mid)
        mep_rows.append(dict(mep_id=int(mid), name=name, country=_country_at(m, "2022-06-01") if m else None,
                             in_mep_dump=bool(m)))
        for g in m.get("Groups") or []:
            gid = g.get("groupid") or "NA"
            if g.get("start", "") <= TERM_END and g.get("end", "9999") >= TERM_START:
                member_of.append(dict(start=int(mid), end=gid if gid in bd.GROUP_ORDER else "NA",
                                      **{"from": g.get("start", "")[:10], "to": (g.get("end") or "")[:10] or None}))
    groups = pd.DataFrame([dict(group_id=g, label=bd.GROUP_LABELS[g]) for g in bd.GROUP_ORDER])
    committees = pd.DataFrame(dict(code=sorted(df.committee.unique())))
    return dict(amendments=pd.DataFrame(am_rows), amends=pd.DataFrame(amends), tabled=pd.DataFrame(tabled),
                meps=pd.DataFrame(mep_rows), member_of=_collapse(pd.DataFrame(member_of)),
                groups=groups, committees=committees)


def _collapse(m: pd.DataFrame) -> pd.DataFrame:
    """One MEMBER_OF per MEP and group: several stints (role changes) collapse to the first start / last end."""
    m = m.assign(to=m["to"].fillna("9999-12-31"))
    m = m.groupby(["start", "end"], as_index=False).agg(**{"from": ("from", "min"), "to": ("to", "max")})
    return m.assign(to=m["to"].replace("9999-12-31", None))


def load_graph(t: dict[str, pd.DataFrame]) -> None:
    graph.apply_schema()
    graph.merge_nodes("Amendment", "am_id", t["amendments"], SOURCE)
    graph.merge_nodes("MEP", "mep_id", t["meps"], SOURCE)
    graph.merge_nodes("Group", "group_id", t["groups"], SOURCE)
    graph.merge_nodes("Committee", "code", t["committees"], SOURCE)
    graph.merge_rels("AMENDS", "Amendment", "am_id", "Provision", "unit_id", t["amends"], SOURCE)
    graph.merge_rels("TABLED", "MEP", "mep_id", "Amendment", "am_id", t["tabled"], SOURCE)
    graph.merge_rels("MEMBER_OF", "MEP", "mep_id", "Group", "group_id", t["member_of"], SOURCE)
    ic = t["amendments"][["am_id", "committee"]].rename(columns={"am_id": "start", "committee": "end"})
    graph.merge_rels("IN_COMMITTEE", "Amendment", "am_id", "Committee", "code", ic, SOURCE)


def main(track: str | None = None) -> None:
    t = build()
    for k, df in t.items():
        df.to_parquet(config.CACHE / f"s03_{k}.parquet", index=False)
    load_graph(t)
    a = t["amendments"]
    print(f"   {len(a)} amendments ({a.is_new.sum()} new provisions, {a.deletion.sum()} deletions), "
          f"{len(t['amends'])} AMENDS ({t['amends'].exact.mean():.1%} exact unit), {len(t['meps'])} MEPs, "
          f"{len(t['tabled'])} TABLED, {len(t['member_of'])} MEMBER_OF, committees {list(t['committees'].code)}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
