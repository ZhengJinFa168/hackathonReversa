#!/usr/bin/env python3
"""
s09_judge.py

Step 1B: High-Speed LLM Judge
Evaluates candidate pairs using a cloud model (OpenRouter) to determine the
relationship, legislative direction, and extract overlapping quotes.
Writes results as [:ECHOED_IN] relationships to Neo4j.
"""

import os
import json
import sqlite3
import hashlib
import pandas as pd
from neo4j import GraphDatabase
from openai import OpenAI
from pydantic import BaseModel, Field
from tqdm import tqdm
from tenacity import retry, stop_after_attempt, wait_exponential
from dotenv import load_dotenv

load_dotenv()

URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
USER = os.getenv("NEO4J_USER", "neo4j")
PASSWORD = os.getenv("NEO4J_PASSWORD", "password123")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

# Default model
LLM_MODEL = os.getenv("LLM_MODEL", "deepseek/deepseek-v4-flash")

# Initialize SQLite Cache
os.makedirs("data", exist_ok=True)
CACHE_DB = "data/llm_cache.sqlite"
conn = sqlite3.connect(CACHE_DB)
conn.execute('''CREATE TABLE IF NOT EXISTS cache
                (hash TEXT PRIMARY KEY, response TEXT)''')
conn.commit()

class JudgeResponse(BaseModel):
    llm_relation: str = Field(description="One of: 'implements', 'partial', 'contradicts', 'unrelated'")
    direction: str = Field(description="One of: 'strengthens_safeguards', 'weakens_obligations', 'neutral'")
    overlap_quote: str = Field(description="The exact text snippet that overlaps between the lobby comment and the amendment. Leave empty if unrelated.")
    reasoning: str = Field(description="Brief 1-sentence reasoning for the classification.")

def get_hash(prompt: str) -> str:
    return hashlib.sha256(prompt.encode('utf-8')).hexdigest()

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
def call_llm(client: OpenAI, chunk_text: str, am_text: str) -> dict:
    prompt = f"""You are an expert EU legislative analyst evaluating lobby influence on the AI Act.
    
Lobbyist Text:
{chunk_text}

MEP Amendment:
{am_text}

Task: Determine if the MEP Amendment implements the Lobbyist's suggestion.
Return ONLY valid JSON according to the schema."""

    prompt_hash = get_hash(prompt + LLM_MODEL)
    
    # Check cache
    cursor = conn.cursor()
    cursor.execute("SELECT response FROM cache WHERE hash=?", (prompt_hash,))
    row = cursor.fetchone()
    if row:
        return json.loads(row[0])

    response = client.beta.chat.completions.parse(
        model=LLM_MODEL,
        messages=[
            {"role": "system", "content": "You are a precise AI that outputs JSON."},
            {"role": "user", "content": prompt}
        ],
        response_format=JudgeResponse,
        temperature=0.0
    )
    
    result = response.choices[0].message.parsed.model_dump()
    
    # Save to cache
    cursor.execute("INSERT OR REPLACE INTO cache VALUES (?, ?)", (prompt_hash, json.dumps(result)))
    conn.commit()
    
    return result

def main():
    if not OPENROUTER_API_KEY:
        print("ERROR: OPENROUTER_API_KEY environment variable not set.")
        # Fallback to mock for hackathon testing if key is missing
        print("WARNING: Using mock LLM responses since no API key is provided.")
        client = None
    else:
        client = OpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=OPENROUTER_API_KEY,
        )

    candidates_path = "data/candidates.parquet"
    if not os.path.exists(candidates_path):
        print(f"ERROR: {candidates_path} not found. Run s07_match.py first.")
        return

    df = pd.read_parquet(candidates_path)
    
    # LIMIT to Top-100 pairs as requested to populate the bubble chart
    df_top = df.head(100)
    print(f"Loaded {len(df)} candidate pairs. Limiting to TOP {len(df_top)} for LLM evaluation.")

    print(f"Evaluating candidates using {LLM_MODEL}...")
    
    updates = []
    
    for idx, row in tqdm(df_top.iterrows(), total=len(df_top)):
        chunk_text = row['chunk_text']
        am_text = row['amendment_text']
        
        # Avoid processing empty texts
        if not chunk_text or not am_text or len(chunk_text) < 10 or len(am_text) < 10:
            continue
            
        if client:
            try:
                res = call_llm(client, chunk_text, am_text)
            except Exception as e:
                print(f"LLM Error on row {idx}: {e}")
                continue
        else:
            # Mock response for hackathon offline testing
            res = {
                "llm_relation": "implements" if row['cosine_score'] > 0.85 else "partial",
                "direction": "weakens_obligations" if "delete" in chunk_text.lower() else "strengthens_safeguards",
                "overlap_quote": "mock overlapping quote",
                "reasoning": "mock reasoning due to missing API key"
            }

        # Determine Tier
        # T1 = verbatim (long overlap run) + implements
        # T2 = substantive echo (implements/partial)
        # T3 = semantic match only (cosine)
        if row['longest_run'] >= 8 and res['llm_relation'] == 'implements':
            tier = 'T1'
        elif res['llm_relation'] in ['implements', 'partial']:
            tier = 'T2'
        else:
            tier = 'T3'

        updates.append({
            "chunk_id": row['chunk_id'],
            "am_id": row['am_id'],
            "score": row['cosine_score'],
            "tier": tier,
            "direction": res['direction'],
            "llm_relation": res['llm_relation'],
            "overlap_quote": res['overlap_quote']
        })

    print(f"Evaluated {len(updates)} pairs. Writing ECHOED_IN to Neo4j...")
    
    driver = GraphDatabase.driver(URI, auth=(USER, PASSWORD))
    with driver.session() as session:
        # Clear old edges first to be idempotent
        session.run("MATCH ()-[r:ECHOED_IN]->() DELETE r")
        
        # Insert new edges
        query = """
        UNWIND $updates AS row
        MATCH (c:Chunk {chunk_id: row.chunk_id})
        MATCH (a:Amendment {am_id: row.am_id})
        MERGE (c)-[r:ECHOED_IN]->(a)
        SET r.score = row.score,
            r.tier = row.tier,
            r.direction = row.direction,
            r.llm_relation = row.llm_relation,
            r.overlap_quote = row.overlap_quote
        """
        session.run(query, updates=updates)
    driver.close()

    print("\nVerification Query:")
    print("MATCH (c:Chunk)-[r:ECHOED_IN]->(a:Amendment) RETURN r.tier, count(r) ORDER BY r.tier;")
    print("S09 Complete!")

if __name__ == "__main__":
    main()
