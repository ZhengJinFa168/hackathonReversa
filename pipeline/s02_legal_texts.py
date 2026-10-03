"""S02 EUR-Lex HTML -> legal units -> Provision text (proposal) and LegalUnit (final act).

Inputs (data_sources/eurlex/raw/, downloaded through the CELLAR API):
  52021PC0206.html + 52021PC0206_annexes.html   Commission proposal COM(2021)206 -> Provision
  02024R1689-20260727.html                      consolidated final act (no recitals, includes M1) -> LegalUnit
  32024R1689.html                               final act as adopted (recitals; "as adopted" text) -> LegalUnit

Every level becomes a unit (article, paragraph, point, sub-point; annex, point...), with `text` = its own
words and `full_text` = its own words plus all descendants. IDs follow pipeline/units.py, i.e. ParlTrack's
location strings, so S03 can join amendments to provisions directly.

Outputs: data_sources/eurlex/proposal_units.csv, final_units.csv (+ parquet copies in data/cache).
"""
from __future__ import annotations

import logging
import re
import warnings
from dataclasses import dataclass, field

import pandas as pd
from bs4 import BeautifulSoup, NavigableString, XMLParsedAsHTMLWarning

from . import config, graph
from .units import parent

warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)
log = logging.getLogger("s02")
SOURCE = "s02_legal_texts"
RAW = config.ROOT / "data_sources" / "eurlex" / "raw"
OUT = config.ROOT / "data_sources" / "eurlex"

BLOCK = {"p", "div", "li", "td", "th", "tr", "table", "h1", "h2", "h3", "h4", "h5", "h6", "br", "dt", "dd", "ul", "ol"}
DROP = (".footnote, .oj-note, .FootnoteReference, .footnoteRef, .oj-note-tag, script, style, "
        ".arrow, .hd-toc-1, .hd-toc-2, .hd-toc-3")

# ---------------------------------------------------------------- HTML -> lines
M1_ON, M1_OFF = "\x01M1\x01", "\x01B\x01"


def html_lines(path) -> list[str]:
    soup = BeautifulSoup(path.read_text(encoding="utf-8"), "lxml")
    for t in soup.select(DROP):
        t.decompose()
    for sup in soup.find_all("sup"):  # footnote numbers
        if re.fullmatch(r"\s*\d{1,3}\s*", sup.get_text()):
            sup.decompose()
    # consolidated-text markers: block markers ▼M1 / ▼B, inline ►M1 … ◄
    for t in soup.select(".modref"):
        txt = t.get_text().strip()
        t.replace_with(NavigableString("\n" + (M1_ON if "M1" in txt else M1_OFF) + "\n"))
    for t in soup.find_all(BLOCK):
        t.insert_before("\n")
        t.insert_after("\n")
    text = soup.get_text("")
    text = text.replace("►M1", "").replace("◄", "").replace("\xa0", " ")
    out = []
    for raw in text.split("\n"):
        line = re.sub(r"\s+", " ", raw).strip()
        if line:
            out.append(line)
    return out


_LABEL_ONLY = re.compile(r"^(\(\s*[0-9a-z]{1,5}\s*\)|[0-9]{1,3}\.|[0-9]{1,3}\)|[a-z]{1,3}\)|—|–|-|•)$")
_LABEL_LEAD = re.compile(r"^(\(\s*[0-9a-z]{1,5}\s*\)|[0-9]{1,3}\.(?!\d))\s*(.+)$")


def merge_labels(lines: list[str]) -> list[str]:
    """'(a)' / '1.' on its own line + the following text line -> one line."""
    out, i = [], 0
    while i < len(lines):
        l = lines[i]
        if _LABEL_ONLY.match(l) and i + 1 < len(lines) and lines[i + 1] not in (M1_ON, M1_OFF):
            out.append(f"{l} {lines[i + 1]}")
            i += 2
        else:
            out.append(l)
            i += 1
    return out


# ---------------------------------------------------------------- state machine
ROMAN_RE = re.compile(r"^(i|ii|iii|iv|v|vi|vii|viii|ix|x|xi|xii|xiii|xiv|xv)$")
_ART = re.compile(r"^Article (\d+)$")
_ANNEX = re.compile(r"^ANNEX ([IVXL]+)\b")
_STRUCT = re.compile(r"^(TITLE|CHAPTER|SECTION|Section|PART|Part) [IVXLC0-9A-Z]+\b")
_REC = re.compile(r"^\((\d{1,3})\)\s*(.+)$")


@dataclass
class Unit:
    unit_id: str
    kind: str
    article: str
    path: list
    text: str = ""
    title: str = ""
    m1: bool = False
    order: int = 0
    children: list = field(default_factory=list)


class Parser:
    def __init__(self, celex: str):
        self.celex = celex
        self.units: dict[str, Unit] = {}
        self.order = 0
        self.m1 = False
        self.cur: Unit | None = None
        self.top: Unit | None = None  # current article / annex
        self.par: str | None = None   # current paragraph label
        self.pt: str | None = None    # current point label
        self.sub: str | None = None   # current sub-point label
        self.want_title = False
        self.flat = False
        self.roman_i: set[int] = set()  # line numbers where "(i)" after "(h)" is a sub-point, not a letter
        self.lineno = 0

    # -- helpers
    def add(self, uid: str, kind: str, path: list, text: str = "") -> Unit:
        u = self.units.get(uid)
        if u is None:
            self.order += 1
            u = Unit(uid, kind, self.top.unit_id if self.top else uid, path, order=self.order)
            self.units[uid] = u
            p = parent(uid)
            if p and p in self.units:
                self.units[p].children.append(uid)
        if text:
            u.text = (u.text + " " + text).strip()
        u.m1 = u.m1 or self.m1
        self.cur = u
        return u

    def append(self, text: str) -> None:
        if self.cur is not None:
            self.cur.text = (self.cur.text + " " + text).strip()
            self.cur.m1 = self.cur.m1 or self.m1

    def is_annex(self) -> bool:
        return self.top is not None and self.top.kind == "annex"

    # -- line handlers
    def start_top(self, uid: str, kind: str) -> None:
        self.top = None
        self.top = self.add(uid, kind, [uid])
        self.par = self.pt = self.sub = None
        self.flat = False
        self.want_title = True

    def label(self, lab: str, rest: str) -> None:
        base = self.top.unit_id
        dotted = lab.endswith(".")
        lab = lab.rstrip(".")
        if self.is_annex():
            # annexes: "1." items are points of an implicit paragraph 1; (a) under them; (i) under those
            if not dotted and self.pt is None and not self.flat:
                self.flat = True  # lettered list straight under the annex heading (Annex I: "(a) ...")
            if self.flat and not dotted:
                self.add(f"{base}({lab})", "point", [base, lab], rest)
                return
            self.flat = False
            self.par = "1"
            if base + "(1)" not in self.units:
                self.add(f"{base}(1)", "paragraph", [base, "1"])
            if dotted or self.pt is None:
                lab = self._dedup(lab, self.pt)
                self.pt, self.sub = lab, None
                self.add(f"{base}(1)({lab})", "point", [base, "1", lab], rest)
            elif self.sub and self._is_sub(lab, self.sub):
                self.add(f"{base}(1)({self.pt})({self.sub})({lab})", "subpoint", [base, "1", self.pt, self.sub, lab], rest)
            else:
                lab = self._dedup(lab, self.sub)
                self.sub = lab
                self.add(f"{base}(1)({self.pt})({lab})", "subpoint", [base, "1", self.pt, lab], rest)
            return
        # articles: "1." paragraph, (a) point, (i) sub-point; Art 3-style (1) definitions with (a) sub-points
        if dotted and lab.isdigit():
            lab = self._dedup(lab, self.par)
            self.par, self.pt, self.sub = lab, None, None
            self.add(f"{base}({lab})", "paragraph", [base, lab], rest)
            return
        if self.par is None:
            self.par = "1"
            self.add(f"{base}(1)", "paragraph", [base, "1"])
        pid = f"{base}({self.par})"
        if self.pt is not None and (self._is_sub(lab, self.pt) or (lab == "i" and self.lineno in self.roman_i)):
            lab = self._dedup(lab, self.sub)
            self.sub = lab
            self.add(f"{pid}({self.pt})({lab})", "subpoint", [base, self.par, self.pt, lab], rest)
            return
        lab = self._dedup(lab, self.pt)
        self.pt, self.sub = lab, None
        self.add(f"{pid}({lab})", "point", [base, self.par, lab], rest)

    @staticmethod
    def _succ(lab: str) -> str:
        romans = ["i", "ii", "iii", "iv", "v", "vi", "vii", "viii", "ix", "x", "xi", "xii", "xiii", "xiv", "xv"]
        if lab.isdigit():
            return str(int(lab) + 1)
        if lab in romans[:-1] and len(lab) > 1:
            return romans[romans.index(lab) + 1]
        if len(lab) == 1 and lab.isalpha():
            return chr(ord(lab) + 1)
        return lab + "+"

    def _dedup(self, lab: str, prev: str | None) -> str:
        """The proposal HTML repeats auto-numbered labels ('(a) (a) (b)', '(1) (1) (3)'): a label equal to
        the previous sibling's is that sibling's successor."""
        return self._succ(prev) if prev is not None and lab == prev else lab

    @staticmethod
    def _is_sub(lab: str, cur: str) -> bool:
        """Is `lab` one level below the current label `cur`?"""
        if cur[:1].isdigit():
            # (40) then (a): sub-point of a numbered point; (14) then (14a): an inserted sibling point
            return not lab[:1].isdigit()
        if ROMAN_RE.match(lab) and not ROMAN_RE.match(cur):
            # (h) then (i) is the next letter, not a sub-point; same for (u)->(v), (w)->(x)
            return not (len(cur) == 1 and len(lab) == 1 and ord(lab) == ord(cur) + 1)
        return False

    def feed(self, line: str) -> None:
        if line == M1_ON:
            self.m1 = True
            return
        if line == M1_OFF:
            self.m1 = False
            return
        if m := _ART.match(line):
            self.start_top(f"Art{int(m.group(1))}", "article")
            return
        if m := _ANNEX.match(line):
            self.start_top(f"Annex{m.group(1)}", "annex")
            rest = line[m.end():].strip()
            if rest:
                self.top.title = rest
                self.want_title = False
            return
        if self.top is None:
            return
        if _STRUCT.match(line) and not self.is_annex():
            self.cur = None  # chapter heading: its title line must not attach to the previous unit
            return
        if self.want_title and not _LABEL_LEAD.match(line):
            self.top.title = line
            self.want_title = False
            return
        self.want_title = False
        if m := _LABEL_LEAD.match(line):
            lab = m.group(1).strip("() ").replace(" ", "")
            if m.group(1).endswith("."):
                lab += "."
            self.label(lab, m.group(2))
            return
        if self.cur is None:
            return
        self.append(line)


def parse_body(lines: list[str], celex: str, stop: tuple[str, ...] = ()) -> dict[str, Unit]:
    p = Parser(celex)
    # "(i)" after "(h)" is ambiguous: it is a roman sub-point when the next label is "(ii)"
    labs = [(i, m.group(1).strip("() ")) for i, l in enumerate(lines) if (m := _LABEL_LEAD.match(l))]
    for (i, a), (_, b) in zip(labs, labs[1:]):
        if a == "i" and b == "ii":
            p.roman_i.add(i)
    for i, l in enumerate(lines):
        if any(l.startswith(s) for s in stop):
            break
        p.lineno = i
        p.feed(l)
    return p.units


def parse_recitals(lines: list[str]) -> dict[str, Unit]:
    units = {}
    try:
        i = next(i for i, l in enumerate(lines) if l.startswith("Whereas"))
    except StopIteration:
        return units
    cur = None
    for l in lines[i + 1 :]:
        if l.startswith("HAVE ADOPTED"):
            break
        if m := _REC.match(l):
            n = int(m.group(1))
            if n == (len(units) + 1):
                cur = Unit(f"Rec{n}", "recital", f"Rec{n}", [f"Rec{n}"], m.group(2), order=n)
                units[cur.unit_id] = cur
                continue
        if cur is not None:
            cur.text += " " + l
    return units


def after(lines: list[str], marker: str) -> list[str]:
    for i, l in enumerate(lines):
        if l.startswith(marker):
            return lines[i + 1 :]
    return lines


def to_frame(units: dict[str, Unit], celex: str) -> pd.DataFrame:
    def full(uid: str) -> str:
        u = units[uid]
        return " ".join([u.text] + [full(c) for c in u.children if c in units]).strip()

    rows = []
    for u in sorted(units.values(), key=lambda u: u.order):
        rows.append(dict(unit_id=u.unit_id, kind=u.kind, article=u.article, path="/".join(u.path),
                         title=u.title or None, text=re.sub(r"\s+", " ", u.text).strip(),
                         full_text=re.sub(r"\s+", " ", full(u.unit_id)).strip(),
                         celex=celex, m1=u.m1, order=u.order))
    return pd.DataFrame(rows)


# ---------------------------------------------------------------- build
def build() -> dict[str, pd.DataFrame]:
    # Commission proposal: recitals + articles (the legislative financial statement follows the articles)
    pl = merge_labels(html_lines(RAW / "52021PC0206.html"))
    rec = parse_recitals(pl)
    arts = parse_body(after(pl, "HAVE ADOPTED"), "52021PC0206", stop=("LEGISLATIVE FINANCIAL STATEMENT",))
    anx = parse_body(merge_labels(html_lines(RAW / "52021PC0206_annexes.html")), "52021PC0206")
    proposal = pd.concat([to_frame(rec, "52021PC0206"), to_frame(arts, "52021PC0206"),
                          to_frame(anx, "52021PC0206")], ignore_index=True)

    # Final act: recitals from the OJ version, articles/annexes from the consolidated text (as in force),
    # plus the OJ articles/annexes as the "as adopted" text for each unit.
    ol = merge_labels(html_lines(RAW / "32024R1689.html"))
    f_rec = to_frame(parse_recitals(ol), "32024R1689")
    f_oj = to_frame(parse_body(after(ol, "HAVE ADOPTED"), "32024R1689"), "32024R1689")
    cl = merge_labels(html_lines(RAW / "02024R1689-20260727.html"))
    f_cons = to_frame(parse_body(cl, "02024R1689-20260727"), "02024R1689-20260727")
    adopted = f_oj.set_index("unit_id")["text"].to_dict()
    f_cons["text_as_adopted"] = f_cons["unit_id"].map(adopted)
    f_cons["consolidated_version"] = "2026-07-27"
    f_rec["text_as_adopted"] = f_rec["text"]
    final = pd.concat([f_rec, f_cons], ignore_index=True)
    # units only in the OJ version (removed or renumbered by M1) are kept too, flagged
    only_oj = f_oj[~f_oj.unit_id.isin(f_cons.unit_id)].assign(text_as_adopted=lambda d: d.text, not_in_force=True)
    final = pd.concat([final, only_oj], ignore_index=True)
    final["law_unit_id"] = "2024/1689:" + final["unit_id"]
    final["mapped_from_unit_id"] = None  # filled by S04 (BECAME)
    final["map_score"] = None
    return {"proposal": proposal, "final": final}


def write_csv(t: dict[str, pd.DataFrame]) -> None:
    t["proposal"][["unit_id", "kind", "article", "path", "text", "celex", "title", "full_text"]].to_csv(
        OUT / "proposal_units.csv", index=False)
    t["final"][["unit_id", "kind", "article", "path", "text", "celex", "mapped_from_unit_id", "map_score",
                "title", "full_text", "m1", "text_as_adopted", "law_unit_id"]].to_csv(OUT / "final_units.csv", index=False)
    for k, df in t.items():
        df.to_parquet(config.CACHE / f"s02_{k}_units.parquet", index=False)


def load_graph(t: dict[str, pd.DataFrame]) -> None:
    graph.apply_schema()
    p = t["proposal"].drop(columns=["order", "celex"]).assign(celex="52021PC0206")
    graph.merge_nodes("Provision", "unit_id", p, SOURCE)
    f = t["final"].drop(columns=["order", "mapped_from_unit_id", "map_score"])
    graph.merge_nodes("LegalUnit", "law_unit_id", f, SOURCE)


# ---------------------------------------------------------------- test against ParlTrack
def check_against_parltrack(proposal: pd.DataFrame) -> dict:
    """Plan test: ParlTrack `old` texts should match the proposal units almost exactly."""
    import json

    from rapidfuzz import fuzz

    from . import sources
    from .units import location_to_unit, resolve

    p = proposal.set_index("unit_id")

    def norm(x: str) -> str:
        x = x.lower().replace("\u2018", "'").replace("\u2019", "'")
        return re.sub(r"[^a-z0-9 ]", "", re.sub(r"\s+", " ", x)).strip()

    def strip_label(x: str) -> str:
        return re.sub(r"^\s*(\(\s*[0-9a-z]{1,5}\s*\)|\d{1,3}\.)\s*", "", x)

    rows = []
    for r in sources.iter_zst_json(sources.path("parltrack_amendments"), json.dumps(config.PROCEDURE)):
        if r.get("reference") != config.PROCEDURE:
            continue
        loc = (r.get("location") or [[None, ""]])[0][-1]
        u = location_to_unit(loc)
        old = " ".join(r.get("old") or [])
        if not u:
            rows.append(dict(kind="not a unit", loc=loc))
            continue
        rid, exact = resolve(u["base_unit_id"] if u["is_new"] else u["unit_id"], p.index)
        if u["is_new"] or not old.strip():
            rows.append(dict(kind="new" if u["is_new"] else "no old text", rid=rid))
            continue
        score = None
        if rid:
            o = norm(strip_label(old))
            score = max(fuzz.ratio(o, norm(p.text[rid])), fuzz.ratio(o, norm(p.full_text[rid])),
                        fuzz.partial_ratio(o, norm(p.full_text[rid])) if len(o) > 40 else 0)
        rows.append(dict(kind="existing", uid=u["unit_id"], rid=rid, exact=exact, score=score, loc=loc))
    d = pd.DataFrame(rows)
    e = d[d.kind == "existing"]
    x = e[e.exact == True].score  # noqa: E712
    out = {
        "amendments": len(d), "by_kind": d.kind.value_counts().to_dict(),
        "existing_exact_unit": round(float(e.exact.mean()), 3),
        "existing_resolved": round(float(e.rid.notna().mean()), 3),
        "exact_score_ge90": round(float((x >= 90).mean()), 3),
        "exact_score_ge80": round(float((x >= 80).mean()), 3),
        "new_with_base_unit": round(float(d[d.kind == "new"].rid.notna().mean()), 3),
        "non_exact_examples": e[e.exact == False].uid.value_counts().head(10).to_dict(),  # noqa: E712
        "low_score_examples": e[(e.exact == True) & (e.score < 80)]["loc"].value_counts().head(8).to_dict(),  # noqa: E712
    }
    return out


def main(track: str | None = None) -> None:
    t = build()
    write_csv(t)
    load_graph(t)
    for k, df in t.items():
        print(f"   {k}: {len(df)} units  " + ", ".join(f"{a}={b}" for a, b in df.kind.value_counts().items()))
    for k, v in check_against_parltrack(t["proposal"]).items():
        print(f"   check {k}: {v}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
