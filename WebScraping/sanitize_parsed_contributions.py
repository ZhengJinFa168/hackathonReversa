#!/usr/bin/env python3
"""
sanitize_parsed_contributions.py
================================
Script per la sanificazione avanzata del testo estratto e dei chunk
nel file `parsed_lobby_contributions.json`.

Rimuove:
  - Sequenze di pallini, trattini, asterischi o puntini decorativi ripetuti (es. • • • • o ------ o .....)
  - Spazi errati attorno ai trattini delle parole composte (es. "future -proof" -> "future-proof")
  - Caratteri di controllo non stampabili
  - Spazi spuri prima della punteggiatura (es. "word , next" -> "word, next")
  - Spazi multipli orizzontali e troppi a capo consecutivi
  - Righe isolate composte esclusivamente da simboli o prive di caratteri alfanumerici

Rigenera:
  - `full_text` sanificato
  - `total_chars` e `total_words` aggiornati
  - `chunks` ri-suddivisi sul testo pulito
"""

import os
import json
import re
import argparse
from typing import List, Dict, Any


def sanitize_extracted_text(text: str) -> str:
    """
    Sanifica e normalizza il testo estratto per analisi NLP e Semantic Search.
    """
    if not text:
        return ""

    # 1. Rimuove sequenze di pallini, trattini, asterischi o puntini decorativi ripetuti (3 o più)
    text = re.sub(r'([•·\-\*\._~=]\s*){3,}', ' ', text)

    # 2. Ripara parole spezzate con trattino a capo o spazi strani (es. "future -proof" -> "future-proof", "high -risk" -> "high-risk")
    # Evita di unire frasi separate da trattino lungo o parentetico (es. "word - word")
    text = re.sub(r'(\b\w+)\s+-(?=[^\s\-])', r'\1-', text)
    text = re.sub(r'(?<=[^\s\-])-\s+(\w+\b)', r'-\1', text)

    # 3. Rimuove caratteri di controllo non stampabili (tranne newline e tabulazione)
    text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]', '', text)

    # 4. Ripara spazi errati prima della punteggiatura (es. "technology , we" -> "technology, we", "certainty ." -> "certainty.")
    text = re.sub(r'\s+([,;:?.!])', r'\1', text)

    # 5. Normalizza spazi multipli orizzontali in un singolo spazio
    text = re.sub(r'[ \t]+', ' ', text)

    # 6. Normalizza troppi a capo consecutivi (massimo 2 per separare i paragrafi)
    text = re.sub(r'\n{3,}', '\n\n', text)

    # 7. Rimuove righe isolate composte solo da simboli decorativi o prive di caratteri alfanumerici
    lines = [line.strip() for line in text.split('\n')]
    cleaned_lines = []
    for line in lines:
        if not line:
            cleaned_lines.append('')
            continue
        # Se la riga non contiene lettere o numeri (es. sola punteggiatura o simboli grafici residui), ignorala
        if not re.search(r'[a-zA-Z0-9]', line):
            continue
        cleaned_lines.append(line)

    joined = '\n'.join(cleaned_lines)
    joined = re.sub(r'\n{3,}', '\n\n', joined)
    return joined.strip()


def chunk_legal_text(text: str, target_words: int = 400, overlap_words: int = 50) -> List[str]:
    """
    Ricalcola i chunk semantici per il testo sanificato.
    """
    if not text or not text.strip():
        return []

    words = text.split()
    if len(words) <= target_words:
        return [text.strip()]

    sentences = re.split(r'(?<=[.!?])\s+', text)
    chunks = []
    current_chunk: List[str] = []
    current_word_count = 0

    for sentence in sentences:
        s_words = sentence.split()
        if not s_words:
            continue

        if len(s_words) > target_words:
            if current_chunk:
                chunks.append(" ".join(current_chunk).strip())
                current_chunk = []
                current_word_count = 0
            step = max(1, target_words - overlap_words)
            for i in range(0, len(s_words), step):
                chunk_slice = s_words[i : i + target_words]
                if chunk_slice:
                    chunks.append(" ".join(chunk_slice).strip())
            continue

        if current_word_count + len(s_words) > target_words and current_chunk:
            chunks.append(" ".join(current_chunk).strip())
            overlap_chunk = []
            overlap_count = 0
            for prev_s in reversed(current_chunk):
                p_len = len(prev_s.split())
                if overlap_count + p_len <= overlap_words or not overlap_chunk:
                    overlap_chunk.insert(0, prev_s)
                    overlap_count += p_len
                else:
                    break
            current_chunk = overlap_chunk
            current_word_count = overlap_count

        current_chunk.append(sentence)
        current_word_count += len(s_words)

    if current_chunk:
        final_chunk = " ".join(current_chunk).strip()
        if not chunks or final_chunk != chunks[-1]:
            chunks.append(final_chunk)

    return chunks


def sanitize_contributions_file(file_path: str, chunk_size: int = 400, chunk_overlap: int = 50):
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"File non trovato: {file_path}")

    with open(file_path, "r", encoding="utf-8") as f:
        records = json.load(f)

    total_chars_before = sum(d.get("total_chars", 0) for d in records)
    records_modified = 0

    for r in records:
        original_full = r.get("full_text", "")
        sanitized_full = sanitize_extracted_text(original_full)

        if sanitized_full != original_full:
            records_modified += 1

        r["full_text"] = sanitized_full
        r["total_chars"] = len(sanitized_full)
        r["total_words"] = len(sanitized_full.split())
        r["has_text"] = len(sanitized_full.strip()) > 10

        # Ricalcola anche i chunks sul testo sanificato
        r["chunks"] = chunk_legal_text(sanitized_full, target_words=chunk_size, overlap_words=chunk_overlap)

    total_chars_after = sum(r.get("total_chars", 0) for r in records)
    total_chunks = sum(len(r.get("chunks", [])) for r in records)

    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)

    print(f"✨ Sanificazione completata per '{file_path}'!")
    print(f"  • Record aggiornati:               {records_modified}/{len(records)}")
    print(f"  • Caratteri totali prima:          {total_chars_before:,}")
    print(f"  • Caratteri totali dopo:           {total_chars_after:,} (rimossi {total_chars_before - total_chars_after:,} caratteri spuri)")
    print(f"  • Chunks totali ricalcolati:       {total_chunks}")


def main():
    parser = argparse.ArgumentParser(description="Sanifica caratteri speciali, pallini e trattini in parsed_lobby_contributions.json")
    parser.add_argument("--file", "-f", default="parsed_lobby_contributions.json", help="File JSON da sanificare")
    parser.add_argument("--chunk-size", "-c", type=int, default=400, help="Dimensione target chunk in parole")
    parser.add_argument("--chunk-overlap", type=int, default=50, help="Overlap chunk in parole")

    args = parser.parse_args()

    script_dir = os.path.dirname(os.path.abspath(__file__))
    target_file = args.file if os.path.exists(args.file) else os.path.join(script_dir, args.file)

    sanitize_contributions_file(target_file, chunk_size=args.chunk_size, chunk_overlap=args.chunk_overlap)


if __name__ == "__main__":
    main()
