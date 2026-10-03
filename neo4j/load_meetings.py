#!/usr/bin/env python3
"""
load_meetings.py

Loads Integrity Watch MEP meetings into Neo4j:
- Matches meetings to existing MEPs (by epid)
- Matches lobbyist names to existing Organizations (using RapidFuzz >= 90)
- Creates (:Organization)-[:MET_WITH]->(:MEP) relationships with properties:
    date, title, location, mep_role, ai_related, period
"""

import os
import pandas as pd
from pathlib import Path
from rapidfuzz import fuzz, process
from neo4j import GraphDatabase

URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
USER = os.getenv("NEO4J_USER", "neo4j")
PASSWORD = os.getenv("NEO4J_PASSWORD", "password123")

MEETINGS_CSV = Path("data_sources/integritywatch/mep_meetings.csv")

def is_ai_related(title: str) -> bool:
    if not title or pd.isna(title):
        return False
    t = str(title).lower()
    if "civil liability" in t:
        return False
    keywords = ["artificial intelligence", "ai act", "algorithm", "intelligence artificielle", "künstliche intelligenz"]
    return any(k in t for k in keywords) or ("\bai\b" in t)

def load_meetings():
    if not MEETINGS_CSV.exists():
        print(f"File {MEETINGS_CSV} not found.")
        return

    print("Connecting to Neo4j...")
    driver = GraphDatabase.driver(URI, auth=(USER, PASSWORD))
    
    with driver.session() as session:
        # Load existing MEPs and Organizations
        db_meps = {r["epid"] for r in session.run("MATCH (m:MEP) RETURN m.ep_id AS epid")}
        db_orgs = {r["name"]: r["org_id"] for r in session.run("MATCH (o:Organization) RETURN o.org_id AS org_id, o.name AS name")}
        org_names = list(db_orgs.keys())
        
        print(f"Loaded {len(db_meps)} MEPs and {len(db_orgs)} Organizations from Neo4j.")

        df = pd.read_csv(MEETINGS_CSV, sep=";", encoding="utf-8-sig")
        df_meps = df[df["epid"].astype(str).isin(db_meps)]
        print(f"Found {len(df_meps)} meetings involving AI Act MEPs.")

        meeting_rows = []
        for _, row in df_meps.iterrows():
            lobbyist_raw = str(row.get("lobbyists", ""))
            if not lobbyist_raw or lobbyist_raw == "nan":
                continue
                
            # Name matching
            match = process.extractOne(lobbyist_raw, org_names, scorer=fuzz.token_set_ratio)
            if match and match[1] >= 90:
                matched_org_name = match[0]
                org_id = db_orgs[matched_org_name]
                date_str = str(row.get("date", ""))
                
                # Period
                period = "2024" if "/24" in date_str or "2024" in date_str else "2020"
                title = str(row.get("title", ""))
                
                meeting_rows.append({
                    "org_id": org_id,
                    "ep_id": str(row["epid"]),
                    "date": date_str,
                    "title": title,
                    "location": str(row.get("location", "")),
                    "mep_role": str(row.get("role", "")),
                    "ai_related": is_ai_related(title),
                    "period": period,
                    "match_score": match[1]
                })

        print(f"Matched {len(meeting_rows)} meetings to consultation lobby organizations.")

        if meeting_rows:
            session.run("""
            UNWIND $rows AS row
            MATCH (o:Organization {org_id: row.org_id})
            MATCH (m:MEP {ep_id: row.ep_id})
            MERGE (o)-[r:MET_WITH {date: row.date, title: row.title}]->(m)
            ON CREATE SET 
                r.location = row.location,
                r.mep_role = row.mep_role,
                r.ai_related = row.ai_related,
                r.period = row.period,
                r.match_score = row.match_score
            """, rows=meeting_rows)
            print("Successfully inserted MET_WITH relationships.")

        count_met = session.run("MATCH ()-[r:MET_WITH]->() RETURN count(r) AS c").single()["c"]
        print(f"Total MET_WITH relationships in graph: {count_met}")

    driver.close()

if __name__ == "__main__":
    load_meetings()
