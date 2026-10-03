#!/usr/bin/env python3
"""
s07_match.py

Step 1A: Vector & Lexical Matching Engine
Extracts the best candidate pairs between lobby Chunks and MEP Amendments.
Uses cosine similarity on embeddings and filters out boilerplate legislative text.
"""

import os
import re
import numpy as np
import pandas as pd
from neo4j import GraphDatabase
from bs4 import BeautifulSoup
from tqdm import tqdm

URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
USER = os.getenv("NEO4J_USER", "neo4j")
PASSWORD = os.getenv("NEO4J_PASSWORD", "password123")

TOP_K_PAIRS = 500

def get_ngrams(words, n=6):
    return set(tuple(words[i:i+n]) for i in range(len(words)-n+1))

def build_stoplist():
    print("Building 6-gram stoplist from original Commission proposal...")
    stoplist = set()
    for filename in ['52021PC0206.html', '52021PC0206_annexes.html']:
        path = os.path.join('data_sources', 'eurlex', 'raw', filename)
        if os.path.exists(path):
            with open(path, 'r', encoding='utf-8') as f:
                soup = BeautifulSoup(f.read(), 'html.parser')
                text = soup.get_text(separator=' ')
                words = re.findall(r'\b\w+\b', text.lower())
                stoplist.update(get_ngrams(words, 6))
    print(f"Stoplist built with {len(stoplist)} unique 6-grams.")
    return stoplist

def mask_boilerplate(words, stoplist, n=6):
    is_boilerplate = [False] * len(words)
    for i in range(len(words) - n + 1):
        ngram = tuple(words[i:i+n])
        if ngram in stoplist:
            for j in range(i, i+n):
                is_boilerplate[j] = True
    return is_boilerplate

def longest_common_substring(list1, list2):
    m, n = len(list1), len(list2)
    # Using 2 rows to save memory instead of m*n matrix
    prev = [0] * (n + 1)
    curr = [0] * (n + 1)
    max_len = 0
    for i in range(1, m + 1):
        for j in range(1, n + 1):
            if list1[i - 1] == list2[j - 1]:
                curr[j] = prev[j - 1] + 1
                max_len = max(max_len, curr[j])
            else:
                curr[j] = 0
        prev, curr = curr, prev
    return max_len

def calculate_overlap(text1, text2, stoplist):
    w1 = re.findall(r'\b\w+\b', text1.lower())
    w2 = re.findall(r'\b\w+\b', text2.lower())
    
    m1 = mask_boilerplate(w1, stoplist)
    m2 = mask_boilerplate(w2, stoplist)
    
    valid_w1 = [w for w, m in zip(w1, m1) if not m]
    valid_w2 = [w for w, m in zip(w2, m2) if not m]
    
    longest_run = longest_common_substring(valid_w1, valid_w2)
    shared_words = len(set(valid_w1).intersection(set(valid_w2)))
    return longest_run, shared_words

def main():
    stoplist = build_stoplist()
    
    driver = GraphDatabase.driver(URI, auth=(USER, PASSWORD))
    with driver.session() as session:
        print("Fetching chunks from Neo4j...")
        # Get chunks with embeddings
        res_chunks = session.run("""
            MATCH (o:Organization)-[:SUBMITTED]->(c:Feedback)<-[:PART_OF]-(ch:Chunk)
            WHERE ch.embedding IS NOT NULL
            RETURN ch.chunk_id AS id, ch.text AS text, o.name AS lobby_name, ch.embedding AS emb
        """)
        chunks = [r.data() for r in res_chunks]
        
        print("Fetching amendments from Neo4j...")
        res_amendments = session.run("""
            MATCH (a:Amendment)
            WHERE a.embedding IS NOT NULL
            OPTIONAL MATCH (m:MEP)-[:TABLED]->(a)
            OPTIONAL MATCH (a)-[:TARGETS]->(p:LawProvision)
            RETURN a.am_id AS id, 
                   CASE 
                     WHEN a.added_text IS NOT NULL AND a.added_text <> "" THEN a.added_text 
                     WHEN a.new_text IS NOT NULL AND a.new_text <> "" THEN a.new_text 
                     ELSE a.old_text 
                   END AS text, 
                   collect(DISTINCT m.name) AS mep_names, collect(DISTINCT p.unit_id) AS articles, a.embedding AS emb
        """)
        amendments = [r.data() for r in res_amendments]
        
    driver.close()
    
    if not chunks or not amendments:
        print("Error: Missing embeddings for chunks or amendments. Did s05_embeddings.py finish successfully?")
        return

    print(f"Loaded {len(chunks)} chunks and {len(amendments)} amendments.")
    
    # Create matrices
    emb_c = np.array([c["emb"] for c in chunks])
    emb_a = np.array([a["emb"] for a in amendments])
    
    print("Calculating cosine similarity matrix...")
    # Normalize vectors
    emb_c_norm = emb_c / np.linalg.norm(emb_c, axis=1, keepdims=True)
    emb_a_norm = emb_a / np.linalg.norm(emb_a, axis=1, keepdims=True)
    
    # Dot product -> cosine similarity
    sim_matrix = np.dot(emb_c_norm, emb_a_norm.T)
    
    print(f"Finding top {TOP_K_PAIRS} candidate pairs...")
    flat_indices = np.argsort(sim_matrix, axis=None)[::-1][:TOP_K_PAIRS]
    c_indices, a_indices = np.unravel_index(flat_indices, sim_matrix.shape)
    
    candidates = []
    print(f"Calculating lexical overlap for {len(c_indices)} pairs...")
    
    for i, j in tqdm(zip(c_indices, a_indices), total=len(c_indices)):
        chunk = chunks[i]
        amendment = amendments[j]
        score = sim_matrix[i, j]
        
        # Calculate overlap
        chunk_text = chunk["text"] or ""
        am_text = amendment["text"] or ""
        longest_run, shared_words = calculate_overlap(chunk_text, am_text, stoplist)
        
        candidates.append({
            "chunk_id": chunk["id"],
            "am_id": amendment["id"],
            "lobby_name": chunk["lobby_name"],
            "mep_name": ", ".join(amendment["mep_names"]) if amendment["mep_names"] else "",
            "article": ", ".join(amendment["articles"]) if amendment["articles"] else "",
            "cosine_score": float(score),
            "longest_run": longest_run,
            "shared_words": shared_words,
            "chunk_text": chunk_text,
            "amendment_text": am_text
        })
        
    df = pd.DataFrame(candidates)
    
    if len(df) > 0:
        # Sort by cosine score
        df = df.sort_values(by="cosine_score", ascending=False).reset_index(drop=True)
        
        # Save to parquet
        os.makedirs('data', exist_ok=True)
        out_path = 'data/candidates.parquet'
        df.to_parquet(out_path)
        print(f"\nSaved {len(df)} candidate pairs to {out_path}.")
        
        print("\n--- TOP 5 CANDIDATE PAIRS ---")
        for idx, row in df.head(5).iterrows():
            print(f"\n[{idx+1}] Score: {row['cosine_score']:.3f} | Overlap Run: {row['longest_run']} words")
            print(f"Lobby: {row['lobby_name']}")
            print(f"MEP: {row['mep_name']} (Art: {row['article']})")
            print(f"Chunk ID: {row['chunk_id']} | Am ID: {row['am_id']}")
            print(f"Snippet Lobby: {row['chunk_text'][:150]}...")
            print(f"Snippet MEP  : {row['amendment_text'][:150]}...")
            print("-" * 50)
    else:
        print("No candidates found above the threshold.")

if __name__ == "__main__":
    main()
