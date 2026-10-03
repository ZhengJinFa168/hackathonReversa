"""S06 Organisations: LobbyFacts + Integrity Watch register -> Organisation properties. Fully automatic.

Every match carries match_method and match_confidence; anything uncertain stays unmatched.
1. Register number (confidence 1.0): the TR number the submitter entered on Have Your Say (S01 already keys
   these organisations by it), joined on LobbyFacts `Identification code` and Integrity Watch `Id`.
2. Number not in either register now: in_register_now = false, money properties stay null.
3. No register number:
   3a exact normalised name (legal forms / brackets removed), unique in the register  -> 0.9
      (also against Have Your Say organisations that did give a number: the same body submitting twice)
   3b acronym in the Have Your Say name = register acronym or leading token, same country -> 0.8
   3c rapidfuzz token_set_ratio >= 95, same country, compatible category, best beats second by >= 5 -> 0.7
   otherwise match_method = none.
4. Merge duplicates: organisations that end up with the same tr_id are merged (apoc.refactor.mergeNodes).
5. Integrity Watch register adds reg_date, register_category, fields_of_interest, mep_meetings_all,
   ec_meetings_vdl1 (2019-2024), and fills money properties for IDs missing from LobbyFacts
   (Costs bands are parsed to their midpoint with costs_is_range = true).
Spend is never imputed: unmatched organisations keep null money properties.
"""
from __future__ import annotations

import logging
import re

import pandas as pd
from rapidfuzz import fuzz, process

from . import config, graph, sources
from .textutil import ascii_fold, normalise_name

log = logging.getLogger("s06")
SOURCE = "s06_orgs"

ISO3 = {"AUT": "AUSTRIA", "BEL": "BELGIUM", "CHE": "SWITZERLAND", "CHN": "CHINA", "CZE": "CZECH REPUBLIC",
        "DEU": "GERMANY", "DNK": "DENMARK", "ESP": "SPAIN", "FIN": "FINLAND", "FRA": "FRANCE",
        "GBR": "UNITED KINGDOM", "GRC": "GREECE", "HRV": "CROATIA", "IRL": "IRELAND", "ITA": "ITALY",
        "JPN": "JAPAN", "LTU": "LITHUANIA", "NLD": "NETHERLANDS", "NOR": "NORWAY", "POL": "POLAND",
        "PRT": "PORTUGAL", "ROU": "ROMANIA", "SVK": "SLOVAKIA", "SWE": "SWEDEN", "USA": "UNITED STATES",
        "LUX": "LUXEMBOURG", "HUN": "HUNGARY", "SVN": "SLOVENIA", "EST": "ESTONIA", "LVA": "LATVIA",
        "BGR": "BULGARIA", "CYP": "CYPRUS", "MLT": "MALTA", "ISR": "ISRAEL", "CAN": "CANADA", "KOR": "KOREA"}
OWN_INTERESTS = "Promotes their own interests or the collective interests of their members"
NON_COMMERCIAL = "Does not represent commercial interests"
COMPATIBLE = {"COMPANY": {OWN_INTERESTS}, "BUSINESS_ASSOCIATION": {OWN_INTERESTS}, "TRADE_UNION": {OWN_INTERESTS},
              "NGO": {NON_COMMERCIAL}, "CONSUMER_ORGANISATION": {NON_COMMERCIAL}}


# ---------------------------------------------------------------- register table
def _num(x):
    try:
        return float(str(x).replace(" ", "").replace(",", "."))
    except (TypeError, ValueError):
        return None


def parse_costs(s) -> tuple[float | None, bool]:
    """'100 000 - 199 999' -> (150000, True); '<10 000' -> (5000, True); '1260031' -> (1260031, False)."""
    if not isinstance(s, str) or not s.strip():
        return None, False
    t = s.replace(" ", "").replace(" ", "")
    if m := re.fullmatch(r"(\d+)-(\d+)", t):
        return (int(m.group(1)) + int(m.group(2)) + 1) / 2, True
    if m := re.fullmatch(r"<(\d+)", t):
        return int(m.group(1)) / 2, True
    if m := re.fullmatch(r">=?(\d+)", t):
        return float(m.group(1)), True
    v = _num(t)
    return v, False


def _acronym(name: str, initials: bool = True) -> set[str]:
    """Acronyms a name carries: bracketed upper-case tokens, an all-caps leading token ('AFNUM - ...'),
    and (register side only) the initials of a 3+ word name."""
    out = set()
    for b in re.findall(r"\(([^)]+)\)", name or ""):
        for tok in re.findall(r"\b[A-Z][A-Z0-9&\-]{1,9}\b", b):
            out.add(tok.replace("-", "").replace("&", ""))
    # Have Your Say side: only an acronym that stands alone or before a separator; register side: any
    # all-caps leading token ("CFE-CGC Confédération ...")
    lead = r"\s*([A-Z][A-Z0-9&\-]{1,9})\b" if initials else r"\s*([A-Z][A-Z0-9&\-]{1,9})(\s+[-\u2013:|(]|\s*$)"
    first = re.match(lead, name or "")
    if first:
        out.add(first.group(1).replace("-", "").replace("&", ""))
    if initials:
        words = [w for w in re.findall(r"[A-Za-z]+", ascii_fold(re.sub(r"\([^)]*\)", " ", name or "")))
                 if w.lower() not in {"of", "and", "for", "the", "de", "des", "du", "la", "le", "et", "fur", "und", "in"}]
        if len(words) >= 3:
            out.add("".join(w[0] for w in words).upper())
    return {a for a in out if len(a) >= 3}


def mnorm(name: str) -> str:
    """Matching normalisation on top of normalise_name: drops a leading 'the' and Dutch 'stichting'."""
    s = normalise_name(name)
    return re.sub(r"^(the|stichting)\s+", "", s)


def register() -> pd.DataFrame:
    lf = sources.load("lobbyfacts").rename(columns={
        "Identification code": "tr_id", "Name": "lf_name", "Members FTE": "lf_fte", "Lobbying cost": "lf_cost",
        "Interest represented": "interest_represented", "Head office": "head_office", "EU office": "eu_office",
        "all EP passes": "ep_passes_all", "Meetings": "ec_meetings_all", "Lobbyfacts URL": "lobbyfacts_url"})
    # "EP passes on <date>" carries the snapshot date in its header
    now_col = next(c for c in lf.columns if c.startswith("EP passes on"))
    lf = lf.rename(columns={now_col: "ep_passes_now"})
    iw = sources.load("integritywatch_register").rename(columns={
        "Id": "tr_id", "Name": "iw_name", "RegDate": "reg_date", "Cat": "register_category", "Cat2": "iw_interest",
        "Country": "iw_country", "FTE": "iw_fte", "FoI": "fields_of_interest", "Costs": "iw_costs",
        "MepMeetingsNum": "mep_meetings_all", "MeetingsNumVonderleyen1": "ec_meetings_vdl1"})
    reg = lf.merge(iw[["tr_id", "iw_name", "reg_date", "register_category", "iw_interest", "iw_country", "iw_fte",
                       "fields_of_interest", "iw_costs", "mep_meetings_all", "ec_meetings_vdl1"]], on="tr_id", how="outer")
    reg["in_lobbyfacts"] = reg.lf_name.notna()
    reg["name"] = reg.lf_name.fillna(reg.iw_name)
    reg["interest"] = reg.interest_represented.fillna(reg.iw_interest)
    reg["countries"] = [{c for c in (a, b, d) if isinstance(c, str)} for a, b, d in
                        zip(reg.head_office, reg.eu_office, reg.iw_country)]
    cost = [parse_costs(c) for c in reg.iw_costs]
    reg["lobbying_cost_eur"] = [(_num(l) if isinstance(l, str) else c[0]) for l, c in zip(reg.lf_cost, cost)]
    reg["costs_is_range"] = [(False if isinstance(l, str) else c[1]) for l, c in zip(reg.lf_cost, cost)]
    reg["fte"] = [(_num(a) if isinstance(a, str) else _num(b)) for a, b in zip(reg.lf_fte, reg.iw_fte)]
    for c in ("ep_passes_now", "ep_passes_all", "ec_meetings_all", "mep_meetings_all", "ec_meetings_vdl1"):
        reg[c] = reg[c].map(_num)
    reg["reg_date"] = pd.to_datetime(reg.reg_date, format="%d/%m/%Y", errors="coerce").dt.date.astype(str).replace("NaT", None)
    reg["norm"] = reg.name.fillna("").map(mnorm)
    reg["acronyms"] = reg.name.fillna("").map(_acronym)
    return reg.reset_index(drop=True)


# ---------------------------------------------------------------- matching
def match_name(org: dict, reg: pd.DataFrame, by_norm: dict, hys_tr_by_norm: dict) -> tuple[str | None, str, float, float | None]:
    """Return (tr_id, method, confidence, fuzzy score) for an organisation without a register number."""
    norm = mnorm(org["name"])
    country = ISO3.get(org.get("country") or "", None)
    ok = COMPATIBLE.get(org.get("user_type"), set())

    def plausible(i: int) -> bool:
        return country in reg.countries.iat[i] and reg.interest.iat[i] in ok

    # 3a exact normalised name: Have Your Say submissions with a number first, then the register (unique only).
    # A one-word name of <= 6 letters is effectively an acronym ("acea"), so it must also agree on country
    # and category.
    if norm and norm in hys_tr_by_norm:
        return hys_tr_by_norm[norm], "exact_name_hys", 0.9, None
    hits = by_norm.get(norm, [])
    if len(hits) > 1 or (" " not in norm and len(norm) <= 6):
        hits = [i for i in hits if plausible(i)] if country else []
    if len(hits) == 1:
        return reg.tr_id.iat[hits[0]], "exact_name", 0.9, None
    if not country:
        return None, "none", 0.0, None
    pool = [i for i in range(len(reg)) if plausible(i)]
    # 3b an acronym the Have Your Say name states explicitly, same country and category
    acr = _acronym(org["name"], initials=False)
    if acr:
        cand = [i for i in pool if acr & reg.acronyms.iat[i]]
        if len(cand) == 1:
            return reg.tr_id.iat[cand[0]], "acronym", 0.8, None
    # 3c fuzzy, same country, compatible category, clear winner. token_set_ratio scores any subset as 100
    # ("European Automobile Manufacturers Association" inside "Japan Automobile Manufacturers Association"),
    # so a subset only counts when one name starts with the other ("LinkedIn" / "LinkedIn Ireland").
    if not pool or not norm:
        return None, "none", 0.0, None
    res = process.extract(norm, {i: reg.norm.iat[i] for i in pool}, scorer=fuzz.token_set_ratio, limit=2)
    best = res[0]
    second = res[1][1] if len(res) > 1 else 0
    other = reg.norm.iat[best[2]]
    aligned = fuzz.token_sort_ratio(norm, other) >= 95 or other.startswith(norm + " ") or norm.startswith(other + " ")
    if best[1] >= 95 and best[1] - second >= 5 and aligned:
        return reg.tr_id.iat[best[2]], "fuzzy", 0.7, best[1]
    return None, "none", 0.0, best[1]


def build() -> tuple[pd.DataFrame, pd.DataFrame]:
    reg = register()
    orgs = pd.concat([pd.read_parquet(config.CACHE / f"s01_orgs_{t}.parquet") for t in "AB"])
    orgs = orgs.groupby("org_id", as_index=False).first()
    reg_ids = set(reg.tr_id)
    by_norm: dict[str, list[int]] = {}
    for i, n in enumerate(reg.norm):
        if n:
            by_norm.setdefault(n, []).append(i)
    hys_tr_by_norm = {mnorm(n): t for n, t in zip(orgs.name, orgs.tr_id) if isinstance(t, str) and mnorm(n)}
    rows = []
    for o in orgs.to_dict("records"):
        if isinstance(o["tr_id"], str):
            m = dict(tr_id=o["tr_id"], match_method="tr_number" if o["tr_id"] in reg_ids else "tr_number_unregistered",
                     match_confidence=1.0, fuzzy_score=None)
        else:
            t, meth, conf, fz = match_name(o, reg, by_norm, hys_tr_by_norm)
            m = dict(tr_id=t, match_method=meth, match_confidence=conf, fuzzy_score=fz)
        rows.append({**o, **m, "old_org_id": o["org_id"]})
    out = pd.DataFrame(rows)
    out["org_id"] = out.tr_id.fillna(out.old_org_id)  # canonical key after matching
    # unmatched submissions of the same body under two spellings ("ACEA (European Automobile ...)" and
    # "European Automobile ... (ACEA)"): same explicit acronym, country and user type -> one node
    seen: dict[tuple, str] = {}
    for i in out.index[out.tr_id.isna()]:
        for a in sorted(_acronym(out.at[i, "name"], initials=False)):
            k = (a, out.at[i, "country"], out.at[i, "user_type"])
            if k in seen and seen[k] != out.at[i, "org_id"]:
                out.at[i, "org_id"] = seen[k]
                out.at[i, "match_method"] = "same_acronym_hys"
                break
            seen.setdefault(k, out.at[i, "org_id"])
    cols = ["tr_id", "lobbying_cost_eur", "costs_is_range", "fte", "interest_represented", "head_office", "ep_passes_now",
            "ep_passes_all", "ec_meetings_all", "ec_meetings_vdl1", "mep_meetings_all", "reg_date", "register_category",
            "fields_of_interest", "lobbyfacts_url", "name", "in_lobbyfacts"]
    out = out.merge(reg[cols].rename(columns={"name": "register_name"}), on="tr_id", how="left")
    out["in_register_now"] = out.register_name.notna()
    return out, reg


def load_graph(out: pd.DataFrame) -> None:
    # 1. re-key name-matched organisations; merge when the target key already exists (duplicates)
    for r in out[out.org_id != out.old_org_id].itertuples():
        graph.run("""
            MATCH (src:Organisation {org_id: $old})
            OPTIONAL MATCH (dst:Organisation {org_id: $new})
            WITH src, dst
            CALL apoc.do.when(dst IS NULL,
              'SET src.org_id = $new RETURN src AS n',
              'CALL apoc.refactor.mergeNodes([dst, src], {properties: "discard", mergeRels: true}) YIELD node RETURN node AS n',
              {src: src, dst: dst, new: $new}) YIELD value
            RETURN count(*)""", old=r.old_org_id, new=r.org_id)
    # 2. properties (one row per canonical organisation; the strongest match wins)
    props = (out.sort_values("match_confidence", ascending=False).drop_duplicates("org_id")
             .drop(columns=["old_org_id", "in_lobbyfacts"]))
    graph.merge_nodes("Organisation", "org_id", props, SOURCE)


def main(track: str | None = None) -> None:
    out, reg = build()
    out.to_parquet(config.CACHE / "s06_org_matches.parquet", index=False)
    load_graph(out)
    n_nodes = graph.run("MATCH (o:Organisation) RETURN count(o) AS n")[0]["n"]
    print(f"   register: {len(reg):,} ids ({reg.in_lobbyfacts.sum():,} in LobbyFacts, {(~reg.in_lobbyfacts).sum()} Integrity Watch only)")
    print(f"   {len(out)} organisations before merging -> {n_nodes} after; match methods: "
          f"{out.match_method.value_counts().to_dict()}")
    print(f"   in register now: {out.drop_duplicates('org_id').in_register_now.sum()} organisations; "
          f"with lobbying cost: {out.drop_duplicates('org_id').lobbying_cost_eur.notna().sum()}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
