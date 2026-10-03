#!/usr/bin/env python3
"""Filter ParlTrack committee amendments for one procedure and build an HTML dashboard.

Usage:
    .venv/bin/python build_dashboard.py [PROCEDURE_REF] [--data-dir DIR] [--out-dir DIR]

Example:
    .venv/bin/python build_dashboard.py "2021/0106(COD)"
"""
import argparse
import difflib
import html
import io
import json
import re
from collections import Counter
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import plotly.offline
import zstandard

HERE = Path(__file__).resolve().parent

# Fixed categorical order (validated palette, light / dark steps).
PALETTE_LIGHT = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"]
PALETTE_DARK = ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300", "#9085e9", "#e66767"]
OTHER_LIGHT, OTHER_DARK = "#8a8984", "#6b6a66"

GROUP_ORDER = ["PPE", "S&D", "RE", "Verts/ALE", "ID", "ECR", "GUE/NGL", "NA"]
GROUP_LABELS = {
    "PPE": "EPP", "S&D": "S&D", "RE": "Renew", "Verts/ALE": "Greens/EFA",
    "ID": "ID", "ECR": "ECR", "GUE/NGL": "The Left", "NA": "Non-attached",
}


# ---------------------------------------------------------------- data loading

def open_dump(data_dir: Path, name: str):
    """Yield one parsed record per line from <name>.json or <name>.json.zst (streamed)."""
    plain, packed = data_dir / f"{name}.json", data_dir / f"{name}.json.zst"
    if plain.exists():
        fh = open(plain, encoding="utf-8")
    elif packed.exists():
        reader = zstandard.ZstdDecompressor().stream_reader(open(packed, "rb"))
        fh = io.TextIOWrapper(reader, encoding="utf-8")
    else:
        raise SystemExit(f"Neither {plain} nor {packed} found")
    with fh:
        for line in fh:
            line = line.strip()
            if not line or line == "]":
                continue
            yield line  # still raw: callers filter cheaply before json.loads


def parse(line: str):
    return json.loads(line[1:])  # drop leading '[' or ','


def load_amendments(data_dir: Path, ref: str):
    needle = json.dumps(ref)  # '"2021/0106(COD)"'
    out = []
    for line in open_dump(data_dir, "ep_amendments"):
        if needle in line:
            rec = parse(line)
            if rec.get("reference") == ref:
                out.append(rec)
    return out


def load_meps(data_dir: Path, wanted: set):
    meps = {}
    for line in open_dump(data_dir, "ep_meps"):
        m = re.match(r'.\{"UserID": (\d+)', line)
        if m and int(m.group(1)) in wanted:
            rec = parse(line)
            meps[rec["UserID"]] = rec
    return meps


def group_at(mep: dict, date: str) -> str:
    for g in mep.get("Groups", []):
        if g.get("start", "") <= date <= g.get("end", "9999"):
            gid = g.get("groupid") or "NA"
            return gid if gid in GROUP_ORDER else "NA"
    return "NA"


# ---------------------------------------------------------------- text helpers

def clean(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def join_lines(lines) -> str:
    """ParlTrack splits PDF text into lines; rejoin, keeping hyphenated breaks glued."""
    out = ""
    for ln in lines or []:
        ln = clean(ln)
        if not ln:
            continue
        if out.endswith("-"):
            out += ln
        else:
            out += (" " if out else "") + ln
    return out


def word_diff(old: str, new: str):
    """Return (old_html, new_html, added_words) with deletions/insertions marked."""
    a, b = old.split(), new.split()
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    old_parts, new_parts, added = [], [], []
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        old_chunk = html.escape(" ".join(a[i1:i2]))
        new_chunk = html.escape(" ".join(b[j1:j2]))
        if op == "equal":
            old_parts.append(old_chunk)
            new_parts.append(new_chunk)
            continue
        if i2 > i1:
            old_parts.append(f"<del>{old_chunk}</del>")
        if j2 > j1:
            new_parts.append(f"<ins>{new_chunk}</ins>")
            added.append(" ".join(b[j1:j2]))
    return " ".join(old_parts), " ".join(new_parts), " … ".join(added)


def provision(loc: str):
    """'Article 5 – paragraph 1 – point d' -> ('Article', '5', 'Article 5')."""
    if not loc:
        return "Unspecified", "", "Unspecified"
    head = re.split(r"\s+[–-]\s+", loc)[0].strip()
    m = re.match(r"(Recital|Article|Annex|Citation|Title|Chapter)\s+([0-9IVXLC]+)\b", head)
    if not m:
        return "Other", "", head
    return m.group(1), m.group(2), head


def roman_to_int(s):
    vals = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100}
    total = 0
    for i, ch in enumerate(s):
        v = vals.get(ch, 0)
        total += -v if i + 1 < len(s) and vals.get(s[i + 1], 0) > v else v
    return total


# ---------------------------------------------------------------- build table

def build_frame(recs, meps):
    rows = []
    for r in recs:
        date = r["date"][:10]
        locs = r.get("location") or []
        loc = clean(locs[0][1]) if locs and len(locs[0]) > 1 else ""
        kind, num, unit = provision(loc)
        old = join_lines(r.get("old"))
        new = join_lines(r.get("new"))
        if new.lower().rstrip(".") == "deleted":  # PDFs print "deleted" for a struck provision
            new = ""
        old_html, new_html, added = word_diff(old, new)
        mep_ids = r.get("meps") or []
        names = [meps[i]["Name"]["full"] if i in meps else str(i) for i in mep_ids]
        groups = [group_at(meps[i], r["date"]) if i in meps else "NA" for i in mep_ids]
        rows.append({
            "id": r["id"],
            "pe": r.get("peid", ""),
            "seq": r.get("seq"),
            "date": date,
            "committee": "-".join(r.get("committee") or []),
            "authors": clean(r.get("authors")),
            "mep_ids": mep_ids,
            "mep_names": names,
            "groups": groups,
            "location": loc or "Unspecified",
            "kind": kind,
            "num": num,
            "provision": unit,
            "is_new": "(new)" in loc,
            "deletion": bool(old) and not new,
            "old": old,
            "new": new,
            "added_words": added,
            "justification": join_lines(r.get("justification")) if isinstance(r.get("justification"), list) else clean(r.get("justification")),
            "old_html": old_html,
            "new_html": new_html,
            "src": r.get("src", ""),
        })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------- charts

def base_layout(fig, title, height=420, **kw):
    fig.update_layout(
        title=dict(text=title, x=0, xanchor="left", font=dict(size=15)),
        height=height,
        margin=dict(l=10, r=20, t=50, b=40),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family="Inter, system-ui, -apple-system, Segoe UI, sans-serif", size=12, color="#52514e"),
        hoverlabel=dict(font=dict(family="Inter, system-ui, sans-serif")),
        bargap=0.25,
        legend=dict(orientation="h", y=-0.15, x=0),
        **kw,
    )
    fig.update_xaxes(gridcolor="#e8e7e3", zerolinecolor="#d4d3cf", linecolor="#d4d3cf")
    fig.update_yaxes(gridcolor="#e8e7e3", zerolinecolor="#d4d3cf", linecolor="#d4d3cf")
    return fig


def fig_timeline(df):
    committees = df["committee"].value_counts().index.tolist()
    fig = go.Figure()
    for i, c in enumerate(committees):
        sub = df[df["committee"] == c].groupby("date").size()
        fig.add_bar(
            x=[pd.Timestamp(d).strftime("%d %b %Y") for d in sub.index], y=sub.values, name=c,
            marker=dict(color=PALETTE_LIGHT[i % 8] if i < 8 else OTHER_LIGHT, line=dict(width=0)),
            hovertemplate=f"<b>{c}</b><br>%{{x}}<br>%{{y:,}} amendments<extra></extra>",
        )
    base_layout(fig, "Amendments tabled, by date and committee", barmode="stack")
    dates = [pd.Timestamp(d).strftime("%d %b %Y") for d in sorted(df["date"].unique())]
    fig.update_xaxes(type="category", categoryorder="array", categoryarray=dates, title_text="Tabling date")
    fig.update_yaxes(title_text="Amendments")
    return fig


def color_for_group(g):
    return PALETTE_LIGHT[GROUP_ORDER.index(g)] if g in GROUP_ORDER else OTHER_LIGHT


def fig_top_meps(df, n=25):
    ex = df[["mep_ids", "mep_names", "groups"]].explode(["mep_ids", "mep_names", "groups"])
    top = (ex.groupby(["mep_ids", "mep_names", "groups"]).size()
             .reset_index(name="n").sort_values("n", ascending=False).head(n))
    top = top.iloc[::-1]
    fig = go.Figure()
    for g in GROUP_ORDER:
        sub = top[top["groups"] == g]
        if sub.empty:
            continue
        fig.add_bar(
            y=sub["mep_names"], x=sub["n"], orientation="h", name=GROUP_LABELS[g],
            marker=dict(color=color_for_group(g), line=dict(width=0)),
            text=sub["n"], textposition="outside", cliponaxis=False,
            hovertemplate="<b>%{y}</b><br>" + GROUP_LABELS[g] + "<br>%{x:,} amendments (co-)signed<extra></extra>",
        )
    base_layout(fig, f"Top {n} MEPs by amendments (co-)signed", height=max(420, 22 * len(top) + 120))
    fig.update_yaxes(categoryorder="array", categoryarray=top["mep_names"].tolist(), gridcolor="rgba(0,0,0,0)")
    fig.update_xaxes(title_text="Amendments (each co-signatory credited)")
    fig.update_layout(legend=dict(orientation="h", y=1.02, x=0, yanchor="bottom"), margin=dict(t=80))
    return fig, top.iloc[::-1]


def fig_groups(df):
    counts = Counter()
    for gs in df["groups"]:
        for g in set(gs):
            counts[g] += 1
    order = [g for g in GROUP_ORDER if counts[g]]
    order.sort(key=lambda g: counts[g])
    fig = go.Figure(go.Bar(
        y=[GROUP_LABELS[g] for g in order], x=[counts[g] for g in order], orientation="h",
        marker=dict(color=[color_for_group(g) for g in order], line=dict(width=0)),
        text=[f"{counts[g]:,}" for g in order], textposition="outside", cliponaxis=False,
        hovertemplate="<b>%{y}</b><br>%{x:,} amendments with ≥1 signatory from this group<extra></extra>",
    ))
    base_layout(fig, "Amendments by political group of signatories", height=380, showlegend=False)
    fig.update_xaxes(title_text="Amendments with at least one signatory from the group")
    fig.update_yaxes(gridcolor="rgba(0,0,0,0)")
    return fig, {GROUP_LABELS[g]: counts[g] for g in reversed(order)}


def fig_provisions(df, kind, title, color):
    sub = df[df["kind"] == kind]
    if sub.empty:
        return None
    g = sub.groupby("num").size().reset_index(name="n")
    key = (lambda s: roman_to_int(s)) if kind == "Annex" else (lambda s: int(s) if s.isdigit() else 0)
    g["k"] = g["num"].map(key)
    g = g.sort_values("k")
    labels = [f"{kind} {x}" for x in g["num"]]
    fig = go.Figure(go.Bar(
        x=g["num"].astype(str), y=g["n"], marker=dict(color=color, line=dict(width=0)),
        customdata=labels,
        hovertemplate="<b>%{customdata}</b><br>%{y:,} amendments<extra></extra>",
    ))
    base_layout(fig, title, height=340, showlegend=False)
    fig.update_xaxes(type="category", title_text=f"{kind} number", tickangle=0,
                     nticks=40 if len(g) > 40 else None)
    fig.update_yaxes(title_text="Amendments")
    return fig


def fig_top_provisions(df, n=20):
    g = df[df["kind"] != "Unspecified"].groupby("location").size().sort_values(ascending=False).head(n).iloc[::-1]
    fig = go.Figure(go.Bar(
        y=g.index, x=g.values, orientation="h", marker=dict(color=PALETTE_LIGHT[0], line=dict(width=0)),
        text=g.values, textposition="outside", cliponaxis=False,
        hovertemplate="<b>%{y}</b><br>%{x:,} amendments<extra></extra>",
    ))
    base_layout(fig, f"Most-amended individual provisions (top {n})", height=max(380, 24 * n + 100), showlegend=False)
    fig.update_yaxes(gridcolor="rgba(0,0,0,0)")
    return fig


# ---------------------------------------------------------------- html

PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Amendments __REF__</title>
<style>
:root {
  color-scheme: light;
  --bg: #f6f5f2; --surface: #fcfcfb; --border: #e3e2de;
  --text: #0b0b0b; --text-2: #52514e; --text-3: #7a7974;
  --accent: #2a78d6; --grid: #e8e7e3;
  --ins-bg: #d6f2e4; --ins-fg: #0d5a3a; --del-bg: #fbe0dd; --del-fg: #8a2420;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    color-scheme: dark;
    --bg: #121211; --surface: #1a1a19; --border: #2e2e2c;
    --text: #ffffff; --text-2: #c3c2b7; --text-3: #8f8e86;
    --accent: #3987e5; --grid: #2c2c2a;
    --ins-bg: #123d2a; --ins-fg: #9be3bf; --del-bg: #45201d; --del-fg: #f2aaa4;
  }
}
:root[data-theme="dark"] {
  color-scheme: dark;
  --bg: #121211; --surface: #1a1a19; --border: #2e2e2c;
  --text: #ffffff; --text-2: #c3c2b7; --text-3: #8f8e86;
  --accent: #3987e5; --grid: #2c2c2a;
  --ins-bg: #123d2a; --ins-fg: #9be3bf; --del-bg: #45201d; --del-fg: #f2aaa4;
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--text);
  font: 14px/1.5 Inter, system-ui, -apple-system, "Segoe UI", sans-serif; }
.wrap { max-width: 1200px; margin: 0 auto; padding: 24px 16px 64px; }
h1 { font-size: 24px; margin: 0 0 4px; letter-spacing: -0.01em; }
.sub { color: var(--text-2); margin: 0 0 20px; }
.tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap: 12px; margin-bottom: 16px; }
.tile, .card { background: var(--surface); border: 1px solid var(--border); border-radius: 10px; }
.tile { padding: 14px 16px; }
.tile .v { font-size: 26px; font-weight: 600; font-variant-numeric: tabular-nums; }
.tile .l { color: var(--text-2); font-size: 12.5px; }
.grid2 { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
@media (max-width: 860px) { .grid2 { grid-template-columns: 1fr; } }
.card { padding: 12px 14px; margin-bottom: 16px; min-width: 0; }
.note { color: var(--text-3); font-size: 12.5px; margin: 4px 2px 0; }
.controls { display: flex; flex-wrap: wrap; gap: 8px; margin: 6px 0 12px; }
.controls input, .controls select { font: inherit; padding: 7px 10px; border-radius: 8px;
  border: 1px solid var(--border); background: var(--bg); color: var(--text); }
.controls input { flex: 1 1 260px; }
.count { color: var(--text-2); font-size: 13px; align-self: center; }
table { width: 100%; border-collapse: collapse; }
th, td { text-align: left; padding: 8px 6px; border-bottom: 1px solid var(--border); vertical-align: top; }
th { font-size: 12px; color: var(--text-2); font-weight: 600; text-transform: uppercase; letter-spacing: .03em; }
tr.row { cursor: pointer; }
tr.row:hover td { background: color-mix(in srgb, var(--accent) 7%, transparent); }
td.num { white-space: nowrap; font-variant-numeric: tabular-nums; color: var(--text-2); }
.snip { color: var(--text-2); }
.diff { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; padding: 6px 0 10px; }
@media (max-width: 700px) { .diff { grid-template-columns: 1fr; } }
.diff h4 { margin: 0 0 4px; font-size: 12px; color: var(--text-2); text-transform: uppercase; letter-spacing: .03em; }
.diff .txt { background: var(--bg); border: 1px solid var(--border); border-radius: 8px; padding: 10px 12px; }
ins { background: var(--ins-bg); color: var(--ins-fg); text-decoration: none; border-radius: 3px; padding: 0 2px; }
del { background: var(--del-bg); color: var(--del-fg); border-radius: 3px; padding: 0 2px; }
mark { background: color-mix(in srgb, #eda100 45%, transparent); color: inherit; border-radius: 2px; }
.meta { font-size: 12.5px; color: var(--text-2); grid-column: 1 / -1; }
.meta a { color: var(--accent); }
.pager { display: flex; gap: 8px; align-items: center; justify-content: flex-end; margin-top: 10px; }
.pager button { font: inherit; padding: 6px 12px; border-radius: 8px; border: 1px solid var(--border);
  background: var(--surface); color: var(--text); cursor: pointer; }
.pager button:disabled { opacity: .4; cursor: default; }
.scroll { overflow-x: auto; }
</style>
<script>__PLOTLYJS__</script>
</head>
<body>
<div class="wrap">
  <h1>Committee amendments · __REF__</h1>
  <p class="sub">__SUBTITLE__</p>
  <div class="tiles">__TILES__</div>

  <div class="card">__FIG_TIMELINE__<p class="note">Committee amendments are tabled in batches, so each committee has one or a few tabling dates.</p></div>
  <div class="grid2">
    <div class="card">__FIG_GROUPS__</div>
    <div class="card">__FIG_TOPPROV__</div>
  </div>
  <div class="card">__FIG_MEPS__<p class="note">Each co-signatory is credited for every amendment they signed. Group = the MEP's political group on the tabling date.</p></div>
  <div class="card">__FIG_ARTICLES__</div>
  <div class="card">__FIG_RECITALS__</div>
  __FIG_ANNEXES__

  <div class="card">
    <h2 style="font-size:15px;margin:4px 0 0">All amendments</h2>
    <div class="controls">
      <input id="q" type="search" placeholder="Search text, MEP, provision… (all words must match)">
      <select id="fc"><option value="">All committees</option></select>
      <select id="fg"><option value="">All groups</option></select>
      <select id="fk"><option value="">All parts</option></select>
      <span class="count" id="count"></span>
    </div>
    <div class="scroll"><table>
      <thead><tr><th>Amendment</th><th>Committee</th><th>Provision</th><th>Signatories</th><th>Words added</th></tr></thead>
      <tbody id="tb"></tbody>
    </table></div>
    <div class="pager"><button id="prev">← Prev</button><span class="count" id="page"></span><button id="next">Next →</button></div>
    <p class="note">Click a row to see the Commission text vs. the amended text. <ins>green</ins> = words the amendment adds, <del>red</del> = words it removes.</p>
  </div>
</div>
<script id="data" type="application/json">__DATA__</script>
<script>
const GROUP_LABELS = __GROUP_LABELS__;
const LIGHT = __PAL_LIGHT__, DARK = __PAL_DARK__;
const rows = JSON.parse(document.getElementById('data').textContent);
rows.forEach(r => { r.hay = [r.id, r.committee, r.location, r.authors, r.mep_names.join(' '), r.old_html.replace(/<[^>]+>/g, ''), r.new_html.replace(/<[^>]+>/g, ''), r.justification].join(' ').toLowerCase(); });

// ---- theme-aware plotly colors
function isDark() {
  const t = document.documentElement.dataset.theme;
  if (t) return t === 'dark';
  return matchMedia('(prefers-color-scheme: dark)').matches;
}
function mapColor(c, toDark) {
  if (Array.isArray(c)) return c.map(x => mapColor(x, toDark));
  if (typeof c !== 'string') return c;
  const from = toDark ? LIGHT : DARK, to = toDark ? DARK : LIGHT;
  const i = from.indexOf(c.toLowerCase());
  return i >= 0 ? to[i] : c;
}
function themePlots() {
  const dark = isDark();
  const cs = getComputedStyle(document.documentElement);
  const text = cs.getPropertyValue('--text-2').trim(), grid = cs.getPropertyValue('--grid').trim();
  document.querySelectorAll('.js-plotly-plot').forEach(gd => {
    const upd = {'font.color': text, 'title.font.color': cs.getPropertyValue('--text').trim(),
      'xaxis.gridcolor': grid, 'yaxis.gridcolor': gd.layout.yaxis && gd.layout.yaxis.gridcolor === 'rgba(0,0,0,0)' ? 'rgba(0,0,0,0)' : grid,
      'xaxis.linecolor': grid, 'yaxis.linecolor': grid, 'xaxis.zerolinecolor': grid, 'yaxis.zerolinecolor': grid,
      'hoverlabel.bgcolor': cs.getPropertyValue('--surface').trim(), 'hoverlabel.font.color': cs.getPropertyValue('--text').trim(),
      'hoverlabel.bordercolor': grid};
    Plotly.relayout(gd, upd);
    gd.data.forEach((tr, i) => Plotly.restyle(gd, {'marker.color': [mapColor(tr.marker.color, dark)]}, [i]));
  });
}
themePlots();
matchMedia('(prefers-color-scheme: dark)').addEventListener('change', themePlots);
new MutationObserver(themePlots).observe(document.documentElement, {attributes: true, attributeFilter: ['data-theme']});

// ---- table
const $ = id => document.getElementById(id);
function fill(sel, vals, label = v => v) { vals.forEach(v => { const o = document.createElement('option'); o.value = v; o.textContent = label(v); sel.appendChild(o); }); }
fill($('fc'), [...new Set(rows.map(r => r.committee))].sort());
fill($('fg'), Object.keys(GROUP_LABELS).filter(g => rows.some(r => r.groups.includes(g))), g => GROUP_LABELS[g]);
fill($('fk'), [...new Set(rows.map(r => r.kind))].sort());
const esc = s => String(s).replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
let page = 0, filtered = rows, open = new Set();
const PER = 40;
function highlight(htmlStr, terms) {
  if (!terms.length) return htmlStr;
  const re = new RegExp('(' + terms.map(t => t.replace(/[.*+?^${}()|[\\]\\\\]/g, '\\\\$&')).join('|') + ')', 'gi');
  return htmlStr.split(/(<[^>]+>)/).map(p => p.startsWith('<') ? p : p.replace(re, '<mark>$1</mark>')).join('');
}
function terms() { return $('q').value.toLowerCase().split(/\\s+/).filter(Boolean); }
function apply() {
  const t = terms(), c = $('fc').value, g = $('fg').value, k = $('fk').value;
  filtered = rows.filter(r => (!c || r.committee === c) && (!g || r.groups.includes(g)) && (!k || r.kind === k) && t.every(w => r.hay.includes(w)));
  page = 0; render();
}
function render() {
  const t = terms();
  const pages = Math.max(1, Math.ceil(filtered.length / PER));
  page = Math.min(page, pages - 1);
  $('count').textContent = filtered.length.toLocaleString() + ' of ' + rows.length.toLocaleString() + ' amendments';
  $('page').textContent = 'Page ' + (page + 1) + ' / ' + pages;
  $('prev').disabled = page === 0; $('next').disabled = page >= pages - 1;
  const out = [];
  filtered.slice(page * PER, page * PER + PER).forEach(r => {
    const names = r.mep_names.length > 3 ? r.mep_names.slice(0, 3).join(', ') + ' +' + (r.mep_names.length - 3) : r.mep_names.join(', ');
    const added = r.deletion ? '<i>deletes the provision</i>' : (r.old_html ? esc(r.added_words).slice(0, 160) : '<i>new provision</i>');
    out.push(`<tr class="row" data-id="${esc(r.id)}"><td class="num">${esc(r.id)}<br>${esc(r.date)}</td><td>${esc(r.committee)}</td><td>${highlight(esc(r.location), t)}</td><td>${highlight(esc(names), t)}</td><td class="snip">${highlight(added, t)}</td></tr>`);
    if (open.has(r.id)) {
      const groups = [...new Set(r.groups)].map(g => GROUP_LABELS[g] || g).join(', ');
      out.push(`<tr><td colspan="5"><div class="diff">
        <div><h4>Text proposed by the Commission</h4><div class="txt">${r.old_html ? highlight(r.old_html, t) : '<i>— (new provision)</i>'}</div></div>
        <div><h4>Amendment</h4><div class="txt">${r.new_html ? highlight(r.new_html, t) : '<i>Deleted</i>'}</div></div>
        <div class="meta"><b>Tabled by:</b> ${highlight(esc(r.authors), t)} · <b>Groups:</b> ${esc(groups)} · <a href="${esc(r.src)}" target="_blank" rel="noopener">source PDF (${esc(r.pe)}, AM ${esc(r.seq)})</a>
        ${r.justification ? '<br><b>Justification:</b> ' + highlight(esc(r.justification), t) : ''}</div>
      </div></td></tr>`);
    }
  });
  $('tb').innerHTML = out.join('') || '<tr><td colspan="5" class="snip">No amendments match.</td></tr>';
}
$('tb').addEventListener('click', e => { const tr = e.target.closest('tr.row'); if (!tr) return; const id = tr.dataset.id; open.has(id) ? open.delete(id) : open.add(id); render(); });
let timer; $('q').addEventListener('input', () => { clearTimeout(timer); timer = setTimeout(apply, 150); });
['fc', 'fg', 'fk'].forEach(id => $(id).addEventListener('change', apply));
$('prev').onclick = () => { page--; render(); }; $('next').onclick = () => { page++; render(); };
apply();
</script>
</body>
</html>
"""


def fig_html(fig):
    if fig is None:
        return ""
    return fig.to_html(full_html=False, include_plotlyjs=False,
                       config={"displaylogo": False, "displayModeBar": False, "responsive": True})


def tile(value, label):
    return f'<div class="tile"><div class="v">{value}</div><div class="l">{html.escape(label)}</div></div>'


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("procedure", nargs="?", default="2021/0106(COD)", help="procedure reference, e.g. 2021/0106(COD)")
    ap.add_argument("--data-dir", type=Path, default=HERE, help="folder with ep_amendments.json[.zst] and ep_meps.json[.zst]")
    ap.add_argument("--out-dir", type=Path, default=HERE / "output")
    args = ap.parse_args()

    ref = args.procedure
    slug = re.sub(r"[^0-9A-Za-z]+", "_", ref).strip("_")
    args.out_dir.mkdir(parents=True, exist_ok=True)

    print(f"Scanning amendments for {ref} …")
    recs = load_amendments(args.data_dir, ref)
    if not recs:
        raise SystemExit(f"No amendments found for {ref}")
    print(f"  {len(recs):,} amendments")
    wanted = {i for r in recs for i in (r.get("meps") or [])}
    print(f"Looking up {len(wanted)} MEPs …")
    meps = load_meps(args.data_dir, wanted)

    df = build_frame(recs, meps)
    df = df.sort_values(["date", "committee", "seq"]).reset_index(drop=True)

    # ---- saved subsets
    raw_path = args.out_dir / f"amendments_{slug}.json"
    with open(raw_path, "w", encoding="utf-8") as fh:
        json.dump(recs, fh, ensure_ascii=False)
    csv_cols = ["id", "pe", "seq", "date", "committee", "location", "provision", "is_new", "deletion",
                "authors", "mep_names", "groups", "old", "new", "added_words", "justification", "src"]
    csv_df = df[csv_cols].copy()
    csv_df["mep_names"] = csv_df["mep_names"].str.join("; ")
    csv_df["groups"] = csv_df["groups"].map(lambda g: "; ".join(GROUP_LABELS.get(x, x) for x in g))
    csv_path = args.out_dir / f"amendments_{slug}.csv"
    csv_df.to_csv(csv_path, index=False)

    # ---- charts
    f_time = fig_timeline(df)
    f_meps, top = fig_top_meps(df)
    f_groups, group_counts = fig_groups(df)
    f_art = fig_provisions(df, "Article", "Amendments per Article", PALETTE_LIGHT[0])
    f_rec = fig_provisions(df, "Recital", "Amendments per Recital", PALETTE_LIGHT[1])
    f_anx = fig_provisions(df, "Annex", "Amendments per Annex", PALETTE_LIGHT[2])
    f_top = fig_top_provisions(df)

    n_meps = len({i for ids in df["mep_ids"] for i in ids})
    tiles = "".join([
        tile(f"{len(df):,}", "committee amendments"),
        tile(f"{df['committee'].nunique()}", "committees (" + ", ".join(df["committee"].value_counts().index) + ")"),
        tile(f"{n_meps:,}", "MEPs signed at least one"),
        tile(f"{(df['kind'] == 'Article').sum():,}", "amend Articles"),
        tile(f"{(df['kind'] == 'Recital').sum():,}", "amend Recitals"),
        tile(f"{df['is_new'].sum():,}", "insert a new provision"),
    ])
    subtitle = (f"Source: ParlTrack committee amendments dump · tabled {df['date'].min()} to {df['date'].max()} · "
                f"{df['pe'].nunique()} amendment documents")

    table_cols = ["id", "pe", "seq", "date", "committee", "location", "kind", "authors", "mep_names", "groups",
                  "old_html", "new_html", "added_words", "justification", "deletion", "src"]
    data_json = json.dumps(df[table_cols].to_dict(orient="records"), ensure_ascii=False).replace("</", "<\\/")

    page = (PAGE
            .replace("__REF__", html.escape(ref))
            .replace("__SUBTITLE__", html.escape(subtitle))
            .replace("__TILES__", tiles)
            .replace("__FIG_TIMELINE__", fig_html(f_time))
            .replace("__FIG_GROUPS__", fig_html(f_groups))
            .replace("__FIG_TOPPROV__", fig_html(f_top))
            .replace("__FIG_MEPS__", fig_html(f_meps))
            .replace("__FIG_ARTICLES__", fig_html(f_art))
            .replace("__FIG_RECITALS__", fig_html(f_rec))
            .replace("__FIG_ANNEXES__", f'<div class="card">{fig_html(f_anx)}</div>' if f_anx else "")
            .replace("__GROUP_LABELS__", json.dumps(GROUP_LABELS))
            .replace("__PAL_LIGHT__", json.dumps(PALETTE_LIGHT))
            .replace("__PAL_DARK__", json.dumps(PALETTE_DARK))
            .replace("__DATA__", data_json)
            .replace("__PLOTLYJS__", plotly.offline.get_plotlyjs()))
    html_path = args.out_dir / f"dashboard_{slug}.html"
    html_path.write_text(page, encoding="utf-8")

    print("\nWrote:")
    for p in (raw_path, csv_path, html_path):
        print(f"  {p}  ({p.stat().st_size / 1e6:.1f} MB)")
    print(f"\nTotal amendments: {len(df):,}")
    print("By committee:", {k: int(v) for k, v in df["committee"].value_counts().items()})
    print("By group (amendments with ≥1 signatory):", group_counts)
    print("Top 5 MEPs:")
    for _, r in top.head(5).iterrows():
        print(f"  {r['mep_names']} ({GROUP_LABELS.get(r['groups'], r['groups'])}): {r['n']}")


if __name__ == "__main__":
    main()
