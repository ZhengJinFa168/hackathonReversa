#!/usr/bin/env python3
import json
import os
import pandas as pd
from neo4j import GraphDatabase
from rapidfuzz import fuzz, process

URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
USER = os.getenv("NEO4J_USER", "neo4j")
PASSWORD = os.getenv("NEO4J_PASSWORD", "password123")

def generate_org_id(name):
    # Generates a slug-like id for organizations if no TR id is present yet.
    if pd.isna(name) or not name:
        return "UNKNOWN_ORG"
    return "ORG_" + "".join([c.upper() for c in str(name) if c.isalnum() or c == ' ']).strip().replace(' ', '_')

def load_feedbacks(session, json_path):
    if not os.path.exists(json_path):
        print(f"Skipping {json_path}, file not found.")
        return

    with open(json_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    print(f"Loading {len(data)} feedbacks from {json_path}...")
    
    batch = []
    chunk_batch = []
    
    for item in data:
        # Organization
        org_name = item.get("organization") or "Unknown"
        org_id = generate_org_id(org_name)
        user_type = item.get("user_type") or "UNKNOWN"
        country = item.get("country") or "UNKNOWN"
        
        # Feedback
        feedback_id = item.get("feedback_id")
        pub_id = item.get("publication_id")
        stage = item.get("stage_name")
        
        batch.append({
            "org_id": org_id,
            "org_name": org_name,
            "user_type": user_type,
            "country": country,
            "feedback_id": feedback_id,
            "pub_id": pub_id,
            "stage": stage
        })
        
        # Chunks
        chunks = item.get("chunks", [])
        for i, text in enumerate(chunks):
            chunk_id = f"{feedback_id}_{i}"
            chunk_batch.append({
                "chunk_id": chunk_id,
                "feedback_id": feedback_id,
                "text": text,
                "order": i,
                "source_type": "attachment" if item.get("has_attachment_file") else "form_text"
            })
            
    # Cypher for Organization and Feedback
    q_org_feedback = """
    UNWIND $batch AS row
    MERGE (o:Organization {org_id: row.org_id})
    ON CREATE SET o.name = row.org_name, o.user_type = row.user_type, o.country = row.country
    MERGE (f:Feedback {feedback_id: row.feedback_id})
    ON CREATE SET f.publication_id = row.pub_id, f.stage_name = row.stage
    MERGE (o)-[:SUBMITTED]->(f)
    """
    
    session.run(q_org_feedback, batch=batch)
    
    # Cypher for Chunks (in chunks of 500)
    q_chunks = """
    UNWIND $batch AS row
    MATCH (f:Feedback {feedback_id: row.feedback_id})
    MERGE (c:Chunk {chunk_id: row.chunk_id})
    ON CREATE SET c.text = row.text, c.order = row.order, c.source_type = row.source_type
    MERGE (c)-[:PART_OF {order: row.order}]->(f)
    """
    
    chunk_size = 500
    for i in range(0, len(chunk_batch), chunk_size):
        session.run(q_chunks, batch=chunk_batch[i:i+chunk_size])
        print(f"  ... inserted chunks {i} to {min(i+chunk_size, len(chunk_batch))}")

def load_lobbyfacts(session, csv_path):
    if not os.path.exists(csv_path):
        print(f"Skipping {csv_path}, file not found.")
        return
        
    print(f"Loading LobbyFacts from {csv_path}...")
    df = pd.read_csv(csv_path)
    
    # Get existing organizations in Neo4j
    result = session.run("MATCH (o:Organization) RETURN o.org_id AS org_id, o.name AS name")
    orgs = [{"org_id": r["org_id"], "name": r["name"]} for r in result]
    
    if not orgs:
        print("No organizations found in Neo4j to match against LobbyFacts.")
        return
        
    # Match names using RapidFuzz
    lf_names = df['Name'].dropna().tolist()
    match_count = 0
    updates = []
    
    for org in orgs:
        db_name = org["name"]
        if db_name == "Unknown":
            continue
            
        # Find best match
        match = process.extractOne(db_name, lf_names, scorer=fuzz.WRatio)
        if match and match[1] >= 90:  # 90% similarity threshold
            lf_row = df[df['Name'] == match[0]].iloc[0]
            updates.append({
                "old_org_id": org["org_id"],
                "new_org_id": str(lf_row['Identification code']),
                "name": db_name,
                "lf_name": lf_row['Name'],
                "costs": float(lf_row['Lobbying cost']) if pd.notna(lf_row['Lobbying cost']) else 0.0,
                "fte": float(lf_row['Members FTE']) if pd.notna(lf_row['Members FTE']) else 0.0,
                "passes": int(lf_row['EP passes on 2026-10-03']) if pd.notna(lf_row['EP passes on 2026-10-03']) else 0
            })
            match_count += 1
            
    print(f"Matched {match_count} organizations with LobbyFacts data.")
    
    # Apply updates to Neo4j using a robust deduplication query
    q_update = """
    UNWIND $updates AS row
    
    // Create or get the official organization node
    MERGE (official_org:Organization {org_id: row.new_org_id})
    ON CREATE SET 
        official_org.name = row.lf_name,
        official_org.lobbying_cost_eur = row.costs,
        official_org.lobbying_costs_eur = row.costs,
        official_org.fte = row.fte,
        official_org.ep_passes = row.passes,
        official_org.ep_passes_all = row.passes
    ON MATCH SET
        official_org.lobbying_cost_eur = row.costs,
        official_org.lobbying_costs_eur = row.costs,
        official_org.fte = row.fte,
        official_org.ep_passes = row.passes,
        official_org.ep_passes_all = row.passes

    WITH row, official_org
    MATCH (old_org:Organization {org_id: row.old_org_id})
    WHERE old_org.org_id <> official_org.org_id
    
    // Transfer scalar properties if missing
    SET official_org.user_type = coalesce(official_org.user_type, old_org.user_type),
        official_org.country = coalesce(official_org.country, old_org.country)
        
    WITH official_org, old_org
    // Transfer relationships
    OPTIONAL MATCH (old_org)-[:SUBMITTED]->(f:Feedback)
    WITH official_org, old_org, collect(f) AS feedbacks
    FOREACH (fb IN feedbacks | MERGE (official_org)-[:SUBMITTED]->(fb))
    
    DETACH DELETE old_org
    """
    
    if updates:
        session.run(q_update, updates=updates)
        print("Organization nodes updated and deduplicated with LobbyFacts metrics.")

def main():
    print("Connecting to Neo4j...")
    driver = GraphDatabase.driver(URI, auth=(USER, PASSWORD))
    
    stage1_json = "WebScraping/feedback/stage1_inception_13340/parsed_feedback_stage1_13340.json"
    stage2_json = "WebScraping/feedback/stage2_proposal_14488/parsed_feedback_stage2_14488.json"
    lobbyfacts_csv = "data_sources/lobbyfacts/lobbyfacts_result.csv"
    
    with driver.session() as session:
        load_feedbacks(session, stage1_json)
        load_feedbacks(session, stage2_json)
        load_lobbyfacts(session, lobbyfacts_csv)
        
        # Verify counts
        org_count = session.run("MATCH (n:Organization) RETURN count(n) AS c").single()["c"]
        fb_count = session.run("MATCH (n:Feedback) RETURN count(n) AS c").single()["c"]
        chunk_count = session.run("MATCH (n:Chunk) RETURN count(n) AS c").single()["c"]
        print(f"\nFinal Graph Database Counts:")
        print(f"  Organizations: {org_count}")
        print(f"  Feedbacks: {fb_count}")
        print(f"  Chunks: {chunk_count}")

    driver.close()
    print("Data loading complete!")

if __name__ == "__main__":
    main()
