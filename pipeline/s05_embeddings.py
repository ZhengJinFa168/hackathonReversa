#!/usr/bin/env python3
"""
s05_embeddings.py

Generates and stores text embeddings in Neo4j for:
1. Feedback chunks (:Chunk)
2. Amendments (:Amendment)

We use the same model (e.g. `intfloat/multilingual-e5-small`) for both so that
they share the same vector space and can be compared via cosine similarity.
The script also creates the corresponding Vector Indexes in Neo4j 5.
"""

import os
import sys
from neo4j import GraphDatabase
import torch
from sentence_transformers import SentenceTransformer
from tqdm import tqdm

URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
USER = os.getenv("NEO4J_USER", "neo4j")
PASSWORD = os.getenv("NEO4J_PASSWORD", "password123")

# Using the small version of E5 for speed and low memory on typical developer machines,
# while maintaining excellent multilingual performance.
# Dimension: 384
MODEL_NAME = "intfloat/multilingual-e5-small"
BATCH_SIZE = 128

def create_vector_indexes(session, dimensions):
    print("Creating vector indexes...")
    # Neo4j 5 syntax for vector indexes
    queries = [
        f"""
        CREATE VECTOR INDEX chunk_embedding IF NOT EXISTS 
        FOR (c:Chunk) ON (c.embedding) 
        OPTIONS {{indexConfig: {{
            `vector.dimensions`: {dimensions},
            `vector.similarity_function`: 'cosine'
        }}}}
        """,
        f"""
        CREATE VECTOR INDEX amendment_embedding IF NOT EXISTS 
        FOR (a:Amendment) ON (a.embedding) 
        OPTIONS {{indexConfig: {{
            `vector.dimensions`: {dimensions},
            `vector.similarity_function`: 'cosine'
        }}}}
        """
    ]
    for q in queries:
        session.run(q)
    print("Vector indexes created/verified.")

def embed_chunks(session, model):
    print("\n--- Embedding Chunks ---")
    # Fetch chunk count
    count = session.run("MATCH (c:Chunk) WHERE c.embedding IS NULL RETURN count(c) AS cnt").single()["cnt"]
    if count == 0:
        print("All chunks already have embeddings.")
        return
        
    print(f"Found {count} chunks to embed.")
    
    # Process in batches
    skip = 0
    limit = 5000
    while True:
        res = session.run(f"""
            MATCH (c:Chunk) 
            WHERE c.embedding IS NULL 
            RETURN c.chunk_id AS id, c.text AS text 
            LIMIT {limit}
        """)
        records = [r for r in res]
        if not records:
            break
            
        texts = []
        for r in records:
            t = r["text"] or ""
            # E5 requires "passage: " prefix for asymmetric tasks
            texts.append("passage: " + t.strip())
            
        print(f"Computing embeddings for batch of {len(texts)} chunks...")
        embeddings = model.encode(texts, batch_size=BATCH_SIZE, show_progress_bar=True, convert_to_numpy=True)
        
        # Write back to Neo4j
        updates = []
        for i, r in enumerate(records):
            updates.append({"id": r["id"], "embedding": embeddings[i].tolist()})
            
        print("Saving to Neo4j...")
        session.run("""
            UNWIND $updates AS row
            MATCH (c:Chunk {chunk_id: row.id})
            SET c.embedding = row.embedding
        """, updates=updates)

def embed_amendments(session, model):
    print("\n--- Embedding Amendments ---")
    # Fetch count
    count = session.run("MATCH (a:Amendment) WHERE a.embedding IS NULL RETURN count(a) AS cnt").single()["cnt"]
    if count == 0:
        print("All amendments already have embeddings.")
        return
        
    print(f"Found {count} amendments to embed.")
    
    skip = 0
    limit = 5000
    while True:
        res = session.run(f"""
            MATCH (a:Amendment) 
            WHERE a.embedding IS NULL 
            RETURN a.am_id AS id, a.added_text AS added_text, a.new_text AS new_text, a.old_text AS old_text 
            LIMIT {limit}
        """)
        records = [r for r in res]
        if not records:
            break
            
        texts = []
        for r in records:
            # We prioritize added_text. If empty, fallback to new_text. If still empty, old_text.
            t = r["added_text"]
            if not t or not t.strip():
                t = r["new_text"]
            if not t or not t.strip():
                t = r["old_text"]
            
            t = t or ""
            # E5 requires "query: " prefix for the query side (what we are looking for)
            # In our case, chunks are passages, and amendments are queries? Or vice-versa.
            # Let's treat amendments as passages as well to ensure symmetric similarity.
            texts.append("passage: " + t.strip())
            
        print(f"Computing embeddings for batch of {len(texts)} amendments...")
        embeddings = model.encode(texts, batch_size=BATCH_SIZE, show_progress_bar=True, convert_to_numpy=True)
        
        updates = []
        for i, r in enumerate(records):
            updates.append({"id": r["id"], "embedding": embeddings[i].tolist()})
            
        print("Saving to Neo4j...")
        session.run("""
            UNWIND $updates AS row
            MATCH (a:Amendment {am_id: row.id})
            SET a.embedding = row.embedding
        """, updates=updates)

def main():
    print("Loading SentenceTransformer model...")
    device = "cuda" if torch.cuda.is_available() else "mps" if torch.backends.mps.is_available() else "cpu"
    print(f"Using device: {device}")
    model = SentenceTransformer(MODEL_NAME, device=device)
    dimensions = model.get_sentence_embedding_dimension()
    print(f"Model {MODEL_NAME} loaded. Dimensions: {dimensions}")

    print("Connecting to Neo4j...")
    driver = GraphDatabase.driver(URI, auth=(USER, PASSWORD))
    
    with driver.session() as session:
        create_vector_indexes(session, dimensions)
        embed_chunks(session, model)
        embed_amendments(session, model)
        
    driver.close()
    print("\nEmbedding process complete!")

if __name__ == "__main__":
    main()
