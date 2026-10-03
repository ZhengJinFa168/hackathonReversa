#!/usr/bin/env python3
"""
load_parltrack.py

Loads European Parliament data for procedure 2021/0106(COD) (AI Act) into Neo4j:
- Political Groups (:PoliticalGroup)
- MEPs (:MEP)
- Amendments (:Amendment)
- Law Provisions (:LawProvision)
- Relationships:
    (:MEP)-[:MEMBER_OF]->(:PoliticalGroup)
    (:MEP)-[:TABLED]->(:Amendment)
    (:Amendment)-[:TARGETS]->(:LawProvision)
"""

import html
import io
import json
import os
import re
import difflib
from pathlib import Path
from neo4j import GraphDatabase
import zstandard

URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
USER = os.getenv("NEO4J_USER", "neo4j")
PASSWORD = os.getenv("NEO4J_PASSWORD", "password123")

AI_ACT_REF = "2021/0106(COD)"

GROUP_MAP = {
    "PPE": {"id": "EPP", "name": "European People's Party (Christian Democrats)"},
    "S&D": {"id": "S&D", "name": "Progressive Alliance of Socialists and Democrats"},
    "RE": {"id": "Renew", "name": "Renew Europe Group"},
    "Renew": {"id": "Renew", "name": "Renew Europe Group"},
    "Verts/ALE": {"id": "Greens/EFA", "name": "Group of the Greens/European Free Alliance"},
    "Greens/EFA": {"id": "Greens/EFA", "name": "Group of the Greens/European Free Alliance"},
    "ID": {"id": "ID", "name": "Identity and Democracy Group"},
    "ECR": {"id": "ECR", "name": "European Conservatives and Reformists Group"},
    "GUE/NGL": {"id": "The Left", "name": "The Left in the European Parliament"},
    "The Left": {"id": "The Left", "name": "The Left in the European Parliament"},
    "NA": {"id": "NI", "name": "Non-attached Members (Non-inscrits)"},
    "NI": {"id": "NI", "name": "Non-attached Members (Non-inscrits)"},
}

def clean(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()

def join_lines(lines) -> str:
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

def word_diff_added(old: str, new: str) -> str:
    a, b = old.split(), new.split()
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    added = []
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if j2 > j1 and op != "equal":
            added.append(" ".join(b[j1:j2]))
    return " … ".join(added)

def parse_provision(loc: str):
    if not loc:
        return "Unspecified", "Unspecified"
    head = re.split(r"\s+[–-]\s+", loc)[0].strip()
    m = re.match(r"(Recital|Article|Annex|Citation|Title|Chapter)\s+([0-9IVXLC]+)\b", head)
    if not m:
        return "Other", head or "Unspecified"
    return m.group(1), head

def load_ep_data(data_dir: Path):
    amendments_file = data_dir / "ep_amendments.json.zst"
    meps_file = data_dir / "ep_meps.json.zst"

    if not amendments_file.exists():
        raise FileNotFoundError(f"Missing {amendments_file}")
    if not meps_file.exists():
        raise FileNotFoundError(f"Missing {meps_file}")

    print(f"Reading amendments from {amendments_file} for procedure {AI_ACT_REF}...")
    reader = zstandard.ZstdDecompressor().stream_reader(open(amendments_file, "rb"))
    fh = io.TextIOWrapper(reader, encoding="utf-8")
    
    needle = json.dumps(AI_ACT_REF)
    raw_amendments = []
    wanted_mep_ids = set()

    for line in fh:
        line = line.strip()
        if not line or line == "]":
            continue
        if needle in line:
            rec = json.loads(line[1:])
            if rec.get("reference") == AI_ACT_REF:
                raw_amendments.append(rec)
                for mid in rec.get("meps", []):
                    wanted_mep_ids.add(mid)

    print(f"Found {len(raw_amendments)} amendments and {len(wanted_mep_ids)} unique MEPs.")

    print(f"Reading MEP data from {meps_file}...")
    reader_meps = zstandard.ZstdDecompressor().stream_reader(open(meps_file, "rb"))
    fh_meps = io.TextIOWrapper(reader_meps, encoding="utf-8")
    
    meps_data = {}
    for line in fh_meps:
        m = re.match(r'.\{"UserID": (\d+)', line)
        if m and int(m.group(1)) in wanted_mep_ids:
            rec = json.loads(line[1:])
            meps_data[rec["UserID"]] = rec

    print(f"Loaded {len(meps_data)} MEP profiles.")
    return raw_amendments, meps_data

def ingest_to_neo4j(raw_amendments, meps_data):
    driver = GraphDatabase.driver(URI, auth=(USER, PASSWORD))
    with driver.session() as session:
        # 1. Groups
        print("\n1. Ingesting Political Groups...")
        unique_groups = set()
        for g_code, g_info in GROUP_MAP.items():
            unique_groups.add((g_info["id"], g_info["name"]))
        
        group_rows = [{"group_id": gid, "name": name} for gid, name in unique_groups]
        session.run("""
        UNWIND $rows AS row
        MERGE (g:PoliticalGroup {group_id: row.group_id})
        ON CREATE SET g.name = row.name
        """, rows=group_rows)
        print(f"  Inserted {len(group_rows)} Political Groups.")

        # 2. MEPs and MEMBER_OF relationships
        print("\n2. Ingesting MEPs and Group memberships...")
        mep_rows = []
        for uid, mep in meps_data.items():
            name = mep.get("Name", {}).get("full") or f"MEP {uid}"
            country = mep.get("Country") or "EU"
            
            # Find dominant / latest group
            latest_group = "NI"
            for g in mep.get("Groups", []):
                gid = g.get("groupid") or "NA"
                if gid in GROUP_MAP:
                    latest_group = GROUP_MAP[gid]["id"]
            
            mep_rows.append({
                "ep_id": str(uid),
                "mep_id": str(uid),
                "name": name,
                "country": country,
                "group_id": latest_group
            })
        
        session.run("""
        UNWIND $rows AS row
        MERGE (m:MEP {ep_id: row.ep_id})
        ON CREATE SET m.name = row.name, m.country = row.country, m.mep_id = row.mep_id
        ON MATCH SET m.name = row.name, m.country = row.country, m.mep_id = row.mep_id
        WITH m, row
        MERGE (g:PoliticalGroup {group_id: row.group_id})
        MERGE (m)-[:MEMBER_OF]->(g)
        """, rows=mep_rows)
        print(f"  Inserted/Updated {len(mep_rows)} MEPs.")

        # 3. Amendments and LawProvisions
        print("\n3. Processing and ingesting Amendments & LawProvisions...")
        am_rows = []
        tabled_rels = []
        provision_set = set()

        for r in raw_amendments:
            am_id = r.get("id") or r.get("peid")
            if not am_id:
                continue
            
            locs = r.get("location") or []
            loc = clean(locs[0][1]) if locs and len(locs[0]) > 1 else ""
            kind, prov_unit = parse_provision(loc)
            provision_set.add((prov_unit, kind))
            
            old = join_lines(r.get("old"))
            new = join_lines(r.get("new"))
            if new.lower().rstrip(".") == "deleted":
                new = ""
            
            added = word_diff_added(old, new)
            committee = "-".join(r.get("committee") or ["IMCO-LIBE"])
            date = r.get("date", "")[:10]
            
            am_rows.append({
                "am_id": am_id,
                "date": date,
                "committee": committee,
                "location": loc or "Unspecified",
                "old_text": old,
                "new_text": new,
                "added_text": added,
                "is_new": "(new)" in loc,
                "deletion": bool(old) and not new,
                "unit_id": prov_unit
            })
            
            # Tabled links
            meps = r.get("meps") or []
            n_meps = len(meps)
            for idx, mid in enumerate(meps):
                tabled_rels.append({
                    "ep_id": str(mid),
                    "am_id": am_id,
                    "is_lead_author": (idx == 0),
                    "weight": 1.0 / n_meps if n_meps > 0 else 1.0
                })

        # Insert LawProvisions
        prov_rows = [{"unit_id": u, "kind": k} for u, k in provision_set]
        session.run("""
        UNWIND $rows AS row
        MERGE (lp:LawProvision {unit_id: row.unit_id})
        ON CREATE SET lp.kind = row.kind
        """, rows=prov_rows)
        print(f"  Inserted {len(prov_rows)} LawProvision units.")

        # Batch insert amendments (in batches of 500)
        batch_size = 500
        for i in range(0, len(am_rows), batch_size):
            batch = am_rows[i:i+batch_size]
            session.run("""
            UNWIND $rows AS row
            MERGE (a:Amendment {am_id: row.am_id})
            ON CREATE SET 
                a.date = row.date,
                a.committee = row.committee,
                a.location = row.location,
                a.old_text = row.old_text,
                a.new_text = row.new_text,
                a.added_text = row.added_text,
                a.is_new = row.is_new,
                a.deletion = row.deletion
            ON MATCH SET
                a.date = row.date,
                a.committee = row.committee,
                a.location = row.location,
                a.old_text = row.old_text,
                a.new_text = row.new_text,
                a.added_text = row.added_text,
                a.is_new = row.is_new,
                a.deletion = row.deletion
            WITH a, row
            MATCH (lp:LawProvision {unit_id: row.unit_id})
            MERGE (a)-[:TARGETS]->(lp)
            """, rows=batch)
            print(f"  Inserted amendments {i} to {min(i+batch_size, len(am_rows))}...")

        # Batch insert TABLED relationships
        for i in range(0, len(tabled_rels), batch_size):
            batch = tabled_rels[i:i+batch_size]
            session.run("""
            UNWIND $rows AS row
            MATCH (m:MEP {ep_id: row.ep_id})
            MATCH (a:Amendment {am_id: row.am_id})
            MERGE (m)-[t:TABLED]->(a)
            ON CREATE SET t.is_lead_author = row.is_lead_author, t.weight = row.weight
            ON MATCH SET t.is_lead_author = row.is_lead_author, t.weight = row.weight
            """, rows=batch)
            print(f"  Inserted TABLED relationships {i} to {min(i+batch_size, len(tabled_rels))}...")

        # Final counts
        print("\nVerifying final graph database counts:")
        for label in ["Organization", "Feedback", "Chunk", "PoliticalGroup", "MEP", "Amendment", "LawProvision"]:
            cnt = session.run(f"MATCH (n:{label}) RETURN count(n) AS c").single()["c"]
            print(f"  {label}: {cnt}")

    driver.close()
    print("\nParlTrack data ingestion complete!")

def main():
    data_dir = Path("amendments")
    raw_amendments, meps_data = load_ep_data(data_dir)
    ingest_to_neo4j(raw_amendments, meps_data)

if __name__ == "__main__":
    main()
