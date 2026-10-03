#!/usr/bin/env python3
"""
apply_schema.py

Applies the Neo4j Cypher schema (constraints and performance indexes)
for the Reversa AI graph model.

Supports either:
1. Direct connection via the `neo4j` Python driver (if installed)
2. Automated fallback via `cypher-shell` (locally or via docker container)
"""

import os
import re
import subprocess
import sys
from pathlib import Path

SCHEMA_FILE = Path(__file__).parent / "schema.cypher"
DEFAULT_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
DEFAULT_USER = os.getenv("NEO4J_USER", "neo4j")
DEFAULT_PASSWORD = os.getenv("NEO4J_PASSWORD", "password123")
DOCKER_CONTAINER = os.getenv("NEO4J_CONTAINER", "neo4j-hackathon")


def parse_cypher_statements(cypher_text: str):
    """Splits Cypher file by semicolons, ignoring comment lines."""
    statements = []
    # Strip single line comments
    clean_lines = []
    for line in cypher_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("//") or not stripped:
            continue
        clean_lines.append(line)
    
    clean_text = "\n".join(clean_lines)
    for raw_stmt in clean_text.split(";"):
        stmt = raw_stmt.strip()
        if stmt:
            statements.append(stmt)
    return statements


def apply_via_python_driver(statements):
    try:
        from neo4j import GraphDatabase
    except ImportError:
        return False

    print(f"🔌 Connecting to Neo4j via Python driver: {DEFAULT_URI}...")
    try:
        driver = GraphDatabase.driver(DEFAULT_URI, auth=(DEFAULT_USER, DEFAULT_PASSWORD))
        with driver.session() as session:
            for stmt in statements:
                summary = session.run(stmt).consume()
            print("✅ All constraints and indexes applied successfully via python driver.")
            
            # Print constraints
            result = session.run("SHOW CONSTRAINTS YIELD name, type, labelsOrTypes, properties RETURN name, type, labelsOrTypes, properties")
            print("\n📋 Active Constraints in Database:")
            for record in result:
                print(f"  • {record['name']} [{record['type']}]: {record['labelsOrTypes']} -> {record['properties']}")
                
        driver.close()
        return True
    except Exception as e:
        print(f"⚠️ Python driver connection failed: {e}")
        return False


def apply_via_cypher_shell():
    print("🚀 Applying schema via cypher-shell...")
    
    # Try via docker container first
    check_docker = subprocess.run(["docker", "ps", "--filter", f"name={DOCKER_CONTAINER}", "--format", "{{.Names}}"],
                                  capture_output=True, text=True)
    if DOCKER_CONTAINER in check_docker.stdout:
        print(f"📦 Found active Docker container '{DOCKER_CONTAINER}', executing schema...")
        cmd = ["docker", "exec", "-i", DOCKER_CONTAINER, "cypher-shell", "-u", DEFAULT_USER, "-p", DEFAULT_PASSWORD]
        with open(SCHEMA_FILE, "r", encoding="utf-8") as f:
            proc = subprocess.run(cmd, stdin=f, capture_output=True, text=True)
        if proc.returncode == 0:
            print("✅ Schema successfully applied in Docker container!")
            print(proc.stdout)
            return True
        else:
            print(f"❌ Error executing in docker: {proc.stderr}")
            return False

    # Try local cypher-shell binary
    try:
        cmd = ["cypher-shell", "-a", DEFAULT_URI, "-u", DEFAULT_USER, "-p", DEFAULT_PASSWORD, "-f", str(SCHEMA_FILE)]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode == 0:
            print("✅ Schema successfully applied via local cypher-shell!")
            return True
        else:
            print(f"❌ Error with local cypher-shell: {proc.stderr}")
            return False
    except FileNotFoundError:
        print("❌ Neither python neo4j driver nor cypher-shell found.")
        return False


def main():
    if not SCHEMA_FILE.exists():
        print(f"❌ Schema file not found: {SCHEMA_FILE}")
        sys.exit(1)

    with open(SCHEMA_FILE, "r", encoding="utf-8") as f:
        cypher_text = f.read()

    statements = parse_cypher_statements(cypher_text)
    print(f"Found {len(statements)} Cypher DDL statements to apply.")

    applied = apply_via_python_driver(statements)
    if not applied:
        applied = apply_via_cypher_shell()

    if not applied:
        print("\n❌ Failed to apply schema automatically. Please apply manually via cypher-shell or Neo4j Browser.")
        sys.exit(1)

    print("\n🎉 Schema initialization complete! The graph structure is ready to receive data.")


if __name__ == "__main__":
    main()
