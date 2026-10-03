#!/usr/bin/env python3
"""
parse_feedback_pdfs.py
======================
Script avanzato di NLP e Data Engineering per l'estrazione, la pulizia, la
normalizzazione e il chunking dei feedback e degli allegati legali (PDF/DOCX)
della consultazione pubblica della Commissione Europea sull'AI Act.

Autore: Antigravity AI (Pair Programming)
Data: Ottobre 2026
"""

import os
import sys
import json
import re
import unicodedata
import argparse
from typing import List, Dict, Any, Optional, Tuple

# Librerie di estrazione PDF e DOCX
try:
    from pypdf import PdfReader
    import pypdf.errors
    PYPDF_AVAILABLE = True
except ImportError:
    PYPDF_AVAILABLE = False

try:
    import pdfplumber
    PDFPLUMBER_AVAILABLE = True
except ImportError:
    PDFPLUMBER_AVAILABLE = False

try:
    import docx
    DOCX_AVAILABLE = True
except ImportError:
    DOCX_AVAILABLE = False

try:
    from tqdm import tqdm
    TQDM_AVAILABLE = True
except ImportError:
    TQDM_AVAILABLE = False


# =====================================================================
# 1. FUNZIONI DI ESTRAZIONE TESTO (PDF & DOCX)
# =====================================================================

def extract_text_from_pdf(pdf_path: str) -> Tuple[List[str], bool, Optional[str]]:
    """
    Estrae il testo pagina per pagina da un file PDF.
    Utilizza pypdf come motore principale e ricorre a pdfplumber come fallback.
    
    Ritorna:
        (pages_text: List[str], has_text: bool, error_msg: Optional[str])
    """
    if not os.path.isfile(pdf_path):
        return [], False, f"File non trovato: {pdf_path}"
    
    pages_text = []
    error_msg = None
    
    # 1. Tentativo con pypdf
    if PYPDF_AVAILABLE:
        try:
            reader = PdfReader(pdf_path)
            for page in reader.pages:
                try:
                    text = page.extract_text() or ""
                    pages_text.append(text)
                except Exception:
                    pages_text.append("")
        except Exception as e:
            error_msg = f"pypdf error: {str(e)}"
            pages_text = []
            
    total_extracted_len = sum(len(p.strip()) for p in pages_text)
    
    # 2. Fallback con pdfplumber se pypdf ha fallito o ha estratto testo quasi nullo
    if total_extracted_len < 50 and PDFPLUMBER_AVAILABLE:
        try:
            plumber_pages = []
            with pdfplumber.open(pdf_path) as pdf:
                for page in pdf.pages:
                    try:
                        text = page.extract_text() or ""
                        plumber_pages.append(text)
                    except Exception:
                        plumber_pages.append("")
            
            plumber_len = sum(len(p.strip()) for p in plumber_pages)
            if plumber_len > total_extracted_len:
                pages_text = plumber_pages
                total_extracted_len = plumber_len
                error_msg = None
        except Exception as e:
            if not error_msg:
                error_msg = f"pdfplumber error: {str(e)}"

    has_text = total_extracted_len >= 30
    return pages_text, has_text, error_msg


def extract_text_from_docx(docx_path: str) -> Tuple[List[str], bool, Optional[str]]:
    """
    Estrae il testo strutturato da un documento DOCX (paragrafi e tabelle).
    
    Ritorna:
        (sections_text: List[str], has_text: bool, error_msg: Optional[str])
    """
    if not os.path.isfile(docx_path):
        return [], False, f"File non trovato: {docx_path}"
    
    if not DOCX_AVAILABLE:
        return [], False, "Libreria python-docx non installata"
    
    try:
        doc = docx.Document(docx_path)
        paragraphs = []
        for p in doc.paragraphs:
            txt = p.text.strip()
            if txt:
                paragraphs.append(txt)
        
        # Estrai anche contenuto da eventuali tabelle
        for table in doc.tables:
            for row in table.rows:
                row_texts = [cell.text.strip() for cell in row.cells if cell.text.strip()]
                if row_texts:
                    paragraphs.append(" | ".join(row_texts))
                    
        total_len = sum(len(p) for p in paragraphs)
        return paragraphs, total_len >= 30, None
    except Exception as e:
        return [], False, f"docx error: {str(e)}"


def resolve_attachment_file(attachment_info: Dict[str, Any], attachments_dir: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Risolve il percorso del file allegato su disco gestendo variazioni di estensioni,
    percorsi relativi e sanitize dei nomi di file.
    
    Ritorna:
        (resolved_path: Optional[str], file_name: Optional[str])
    """
    candidates = []
    
    # Campi possibili dal JSON
    file_name = attachment_info.get("fileName")
    ers_file_name = attachment_info.get("ersFileName")
    local_path = attachment_info.get("localFilePath")
    
    if local_path:
        candidates.append(local_path)
        candidates.append(os.path.join(attachments_dir, os.path.basename(local_path)))
    if file_name:
        candidates.append(os.path.join(attachments_dir, file_name))
    if ers_file_name:
        candidates.append(os.path.join(attachments_dir, ers_file_name))
        
    # Aggiungi varianti .pdf / .docx se ci fossero discrepanze di estensione
    expanded_candidates = list(candidates)
    for c in candidates:
        base, ext = os.path.splitext(c)
        if ext.lower() == ".docx":
            expanded_candidates.append(base + ".pdf")
        elif ext.lower() == ".pdf":
            expanded_candidates.append(base + ".docx")

    for path in expanded_candidates:
        if path and os.path.isfile(path):
            return os.path.abspath(path), os.path.basename(path)
            
    # Prova ricerca case-insensitive nella cartella
    if os.path.isdir(attachments_dir) and (file_name or ers_file_name):
        target_name = (file_name or ers_file_name).lower()
        target_base = os.path.splitext(target_name)[0]
        for existing_file in os.listdir(attachments_dir):
            existing_lower = existing_file.lower()
            if existing_lower == target_name or os.path.splitext(existing_lower)[0] == target_base:
                full_p = os.path.join(attachments_dir, existing_file)
                return os.path.abspath(full_p), existing_file
                
    chosen_name = file_name or ers_file_name or (os.path.basename(local_path) if local_path else None)
    return None, chosen_name


# =====================================================================
# 2. PULIZIA DEL TESTO LEGALE ED ELIMINAZIONE HEADER/FOOTER
# =====================================================================

def clean_page_text(page_text: str) -> str:
    """
    Pulisce il testo di una singola pagina:
    - Normalizzazione Unicode (NFKC)
    - Sostituzione ligature tipografiche (fi, fl, ffi...)
    - Rimozione numerazione di pagina e header ripetitivi standard
    """
    if not page_text:
        return ""
    
    # 1. Normalizzazione Unicode
    text = unicodedata.normalize("NFKC", page_text)
    
    # 2. Mappatura ligature e virgolette tipografiche
    replacements = {
        "ﬁ": "fi", "ﬂ": "fl", "ﬀ": "ff", "ﬃ": "ffi", "ﬄ": "ffl",
        "\u00a0": " ", "\u200b": "", "\ufeff": "", "\r": "",
        "“": '"', "”": '"', "„": '"', "«": '"', "»": '"',
        "‘": "'", "’": "'", "ʻ": "'", "ʼ": "'",
        "–": "-", "—": "-", "―": "-", "•": "• ", "\t": " "
    }
    for k, v in replacements.items():
        text = text.replace(k, v)
        
    lines = [line.strip() for line in text.split("\n")]
    cleaned_lines = []
    
    for line in lines:
        if not line:
            cleaned_lines.append("")
            continue
            
        # Rimozione "Page X of Y", "Page X", "- X -", numeri isolati
        if re.match(r"^(?:page|p\.)\s*\d+\s*(?:of|/)\s*\d+$", line, re.IGNORECASE):
            continue
        if re.match(r"^(?:page|p\.)\s*\d+$", line, re.IGNORECASE):
            continue
        if re.match(r"^[-\u2013\u2014]\s*\d+\s*[-\u2013\u2014]$", line):
            continue
        if re.match(r"^\d{1,3}$", line): # Numero di pagina isolato
            continue
            
        # Rimozione timbro Ares standard UE
        if re.match(r"^Ref\.\s*Ares\(\d+\)\d+\s*-\s*\d{2}/\d{2}/\d{4}$", line):
            continue
            
        cleaned_lines.append(line)
        
    return "\n".join(cleaned_lines)


def filter_repetitive_headers_footers(pages_text: List[str]) -> List[str]:
    """
    Rileva e rimuove header o footer che si ripetono identici su più pagine.
    """
    if len(pages_text) <= 2:
        return pages_text

    header_candidates: Dict[str, int] = {}
    footer_candidates: Dict[str, int] = {}

    for page in pages_text:
        lines = [l.strip() for l in page.split("\n") if l.strip()]
        if lines:
            first_line = lines[0]
            last_line = lines[-1]
            if len(first_line) < 120:
                header_candidates[first_line] = header_candidates.get(first_line, 0) + 1
            if len(last_line) < 120:
                footer_candidates[last_line] = footer_candidates.get(last_line, 0) + 1

    # Identifica pattern che compaiono in almeno il 40% delle pagine
    threshold = max(2, int(len(pages_text) * 0.4))
    repeated_headers = {k for k, count in header_candidates.items() if count >= threshold}
    repeated_footers = {k for k, count in footer_candidates.items() if count >= threshold}

    filtered_pages = []
    for page in pages_text:
        lines = [l.strip() for l in page.split("\n")]
        valid_lines = []
        for l in lines:
            if l in repeated_headers or l in repeated_footers:
                continue
            valid_lines.append(l)
        filtered_pages.append("\n".join(valid_lines))

    return filtered_pages


def sanitize_extracted_text(text: str) -> str:
    """
    Sanifica e normalizza il testo estratto per analisi NLP e Semantic Search:
    - Rimuove sequenze di pallini, trattini o puntini decorativi ripetuti (es. • • • • o ------ o .....)
    - Ripara parole spezzate con trattino a capo o spazi strani (es. 'future -proof' -> 'future-proof')
    - Rimuove caratteri di controllo non stampabili
    - Ripara spazi spuri prima della punteggiatura (es. 'technology , we' -> 'technology, we')
    - Normalizza spazi multipli e righe prive di contenuto alfanumerico
    """
    if not text:
        return ""

    # 1. Rimuove sequenze di pallini, trattini, asterischi o puntini decorativi ripetuti (3 o più)
    text = re.sub(r'([•·\-\*\._~=]\s*){3,}', ' ', text)

    # 2. Ripara parole spezzate con trattino a capo o spazi strani (es. "future -proof" -> "future-proof", "high -risk" -> "high-risk")
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
        if not re.search(r'[a-zA-Z0-9]', line):
            continue
        cleaned_lines.append(line)

    joined = '\n'.join(cleaned_lines)
    joined = re.sub(r'\n{3,}', '\n\n', joined)
    return joined.strip()


def postprocess_legal_text(text: str) -> str:
    """
    Unisce le righe spezzate all'interno dei paragrafi, ricompone le parole
    sillabate a fine riga e normalizza la spaziatura conservando elenchi puntati.
    """
    if not text or not text.strip():
        return ""
    
    # 1. Ricomposizione parole sillabate: es. "regu-\nlation" -> "regulation"
    text = re.sub(r'(\b[a-zA-Z]{2,})-\s*\n\s*([a-zA-Z]{2,}\b)', r'\1\2', text)
    
    # 2. Suddivisione in blocchi di paragrafi
    raw_paragraphs = re.split(r'\n\s*\n+', text)
    cleaned_paragraphs = []
    
    for p in raw_paragraphs:
        p = p.strip()
        if not p:
            continue
            
        lines = p.split('\n')
        processed_lines = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            # Se la riga inizia con un punto elenco o lettera/numero di sezione, preserva la riga
            if re.match(r'^([•\-\*■\u2022]|\d+[\.\)]|[a-zA-Z][\.\)])\s+', line):
                processed_lines.append('\n' + line)
            else:
                processed_lines.append(line)
                
        joined = ' '.join(processed_lines)
        # Normalizza a capo preservati
        joined = re.sub(r'\s*\n\s*', '\n', joined)
        # Collassa spazi orizzontali multipli
        joined = re.sub(r'[ \t]+', ' ', joined)
        cleaned_paragraphs.append(joined.strip())
        
    combined = '\n\n'.join(cleaned_paragraphs).strip()
    return sanitize_extracted_text(combined)


# =====================================================================
# 3. UNIONE CONTENUTO (FULL TEXT) & RICONOSCIMENTO PLACEHOLDER
# =====================================================================

def is_placeholder_feedback(feedback: str) -> bool:
    """
    Verifica se il testo immesso nel form web è un semplice segnaposto che rinvia all'allegato.
    """
    if not feedback:
        return True
    f_lower = feedback.strip().lower()
    if len(f_lower) < 140 and any(kw in f_lower for kw in [
        "attached", "attachment", "enclosed", "find attached", "find enclosed",
        "see file", "see attached", "in the attached", "pleas find", "please see",
        "detailed input in the", "comments in the attached", "see the attached",
        "refer to our attached", "please refer to"
    ]):
        return True
    return False


def combine_feedback_and_attachment(web_feedback: str, attachment_text: str, attachment_name: Optional[str]) -> str:
    """
    Combina il commento web e il testo dell'allegato in modo coerente e pulito.
    """
    web_clean = postprocess_legal_text(clean_page_text(web_feedback)) if web_feedback else ""
    att_clean = attachment_text.strip() if attachment_text else ""
    
    if att_clean:
        if is_placeholder_feedback(web_clean):
            return att_clean
        # Se il feedback web è già una porzione dell'allegato (es. introduzione duplicata)
        if web_clean and len(web_clean) > 50 and (web_clean[:100].lower() in att_clean.lower()):
            return att_clean
        if web_clean:
            return f"{web_clean}\n\n{att_clean}"
        return att_clean
    else:
        return web_clean


# =====================================================================
# 4. ESTRAZIONE RIFERIMENTI NORMATIVI (REGEX)
# =====================================================================

def extract_legal_references(text: str) -> Dict[str, List[str]]:
    """
    Rileva e normalizza i riferimenti agli Articoli, Allegati e Considerando citati.
    """
    if not text:
        return {"detected_articles": [], "detected_annexes": [], "detected_recitals": []}

    articles = set()
    annexes = set()
    recitals = set()

    # 1. Articoli singoli o specifici (es. Article 6, Art. 52, Art. 7(2)(a))
    for match in re.finditer(r'\b(?:Article|Art\.|Articolo|Artikel)\s*([0-9]+[a-z]?(?:\s*\(\s*[0-9a-zA-Z]+\s*\))*)', text, re.IGNORECASE):
        art_ref = re.sub(r'\s+', '', match.group(1).strip())
        articles.add(f"Article {art_ref}")

    # 2. Articoli multipli (es. Articles 5, 6 and 7 / Articles 14 to 16)
    for match in re.finditer(r'\b(?:Articles|Articoli)\s+([0-9]+(?:\s*(?:,|and|or|to|\u2013|-)\s*[0-9]+)+)', text, re.IGNORECASE):
        nums = re.findall(r'\b[0-9]+\b', match.group(1))
        for n in nums:
            articles.add(f"Article {n}")

    # 3. Allegati (es. Annex III, Annexes I and II, Allegato 2)
    for match in re.finditer(r'\b(?:Annex|Annexes|Allegato|Anhang)\s+([IVXLCDM]+|[0-9]+|[A-E])\b', text, re.IGNORECASE):
        ann_ref = match.group(1).strip().upper()
        annexes.add(f"Annex {ann_ref}")

    for match in re.finditer(r'\b(?:Annexes|Allegati)\s+([IVXLCDM0-9A-E,\s\u2013\-andor]+)', text, re.IGNORECASE):
        tokens = re.findall(r'\b([IVXLCDM]+|[0-9]+|[A-E])\b', match.group(1), re.IGNORECASE)
        for t in tokens:
            annexes.add(f"Annex {t.upper()}")

    # 4. Considerando (es. Recital 12, Considerando 4)
    for match in re.finditer(r'\b(?:Recital|Considerando|Erwaegungsgrund|Erwägungsgrund)\s+([0-9]+)\b', text, re.IGNORECASE):
        rec_num = match.group(1).strip()
        recitals.add(f"Recital {rec_num}")

    for match in re.finditer(r'\b(?:Recitals|Considerandi)\s+([0-9]+(?:\s*(?:,|and|or|to|\u2013|-)\s*[0-9]+)+)', text, re.IGNORECASE):
        nums = re.findall(r'\b[0-9]+\b', match.group(1))
        for n in nums:
            recitals.add(f"Recital {n}")

    def natural_sort_key(s: str):
        return [int(t) if t.isdigit() else t.lower() for t in re.split(r'(\d+)', s)]

    return {
        "detected_articles": sorted(list(articles), key=natural_sort_key),
        "detected_annexes": sorted(list(annexes), key=natural_sort_key),
        "detected_recitals": sorted(list(recitals), key=natural_sort_key)
    }


# =====================================================================
# 5. CHUNKING INTELLIGENTE PER EMBEDDINGS (300-500 PAROLE + OVERLAP)
# =====================================================================

def chunk_legal_text(text: str, target_words: int = 400, overlap_words: int = 50) -> List[str]:
    """
    Divide il testo in chunk semantici logici compresi tra 300 e 500 parole
    con un overlap morbido a livello di frase per massimizzare le prestazioni
    dei modelli di embedding e semantic retrieval.
    """
    if not text or not text.strip():
        return []

    words = text.split()
    if len(words) <= target_words:
        return [text.strip()]

    # Dividi per frasi preservando la punteggiatura
    sentences = re.split(r'(?<=[.!?])\s+', text)
    chunks = []
    current_chunk: List[str] = []
    current_word_count = 0

    for sentence in sentences:
        s_words = sentence.split()
        if not s_words:
            continue

        # Se una singola frase è insolitamente lunga, dividila per parole
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
            # Calcola overlap a livello di frasi finali
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


# =====================================================================
# 6. PIPELINE DI ELABORAZIONE PRINCIPALE
# =====================================================================

def process_lobby_feedbacks(
    input_json_path: str,
    attachments_dir: str,
    output_json_path: str,
    target_chunk_words: int = 400,
    overlap_words: int = 50
) -> Dict[str, Any]:
    """
    Esegue l'intera pipeline di parsing, estrazione, pulizia, regex e chunking.
    """
    if not os.path.isfile(input_json_path):
        raise FileNotFoundError(f"File di input non trovato: {input_json_path}")

    with open(input_json_path, "r", encoding="utf-8") as f:
        records = json.load(f)

    print(f"📖 Caricati {len(records)} record di feedback da '{input_json_path}'")
    print(f"📁 Cartella allegati: '{attachments_dir}'")
    print(f"⚙️  Chunking: ~{target_chunk_words} parole (overlap: {overlap_words} parole)\n")

    parsed_records = []
    stats = {
        "total_records": len(records),
        "attachments_found_on_disk": 0,
        "attachments_missing_on_disk": 0,
        "records_with_extracted_text": 0,
        "records_with_fallback_only": 0,
        "total_chunks_created": 0,
        "total_articles_detected": 0,
        "total_annexes_detected": 0
    }

    iterator = enumerate(records, start=1)
    if TQDM_AVAILABLE:
        iterator = tqdm(iterator, total=len(records), desc="🔍 Elaborazione Feedback")

    for idx, item in iterator:
        feedback_id = item.get("id")
        org_name = item.get("organization") or "Unknown"
        user_type = item.get("userType") or "UNKNOWN"
        country = item.get("country") or ""
        raw_web_feedback = item.get("feedback") or ""
        attachments_list = item.get("attachments") or []

        has_attachment_decl = len(attachments_list) > 0
        has_attachment_file = False
        attachment_name = None
        attachment_clean_text = ""
        extraction_error = None

        if has_attachment_decl:
            primary_attachment = attachments_list[0]
            resolved_path, attachment_name = resolve_attachment_file(primary_attachment, attachments_dir)

            if resolved_path:
                has_attachment_file = True
                stats["attachments_found_on_disk"] += 1
                ext = os.path.splitext(resolved_path)[1].lower()

                if ext == ".docx":
                    docx_paras, has_txt, err = extract_text_from_docx(resolved_path)
                    if has_txt:
                        cleaned_paras = [clean_page_text(p) for p in docx_paras]
                        attachment_clean_text = postprocess_legal_text("\n\n".join(cleaned_paras))
                    extraction_error = err
                else: # Default PDF
                    pdf_pages, has_txt, err = extract_text_from_pdf(resolved_path)
                    if has_txt:
                        cleaned_pages = [clean_page_text(p) for p in pdf_pages]
                        filtered_pages = filter_repetitive_headers_footers(cleaned_pages)
                        attachment_clean_text = postprocess_legal_text("\n\n".join(filtered_pages))
                    extraction_error = err
            else:
                stats["attachments_missing_on_disk"] += 1

        # Combina testo web e allegato
        full_text = combine_feedback_and_attachment(raw_web_feedback, attachment_clean_text, attachment_name)
        has_text = len(full_text.strip()) > 10

        if has_text:
            stats["records_with_extracted_text"] += 1
            if not attachment_clean_text:
                stats["records_with_fallback_only"] += 1

        # Estrazione riferimenti legali
        legal_refs = extract_legal_references(full_text)
        stats["total_articles_detected"] += len(legal_refs["detected_articles"])
        stats["total_annexes_detected"] += len(legal_refs["detected_annexes"])

        # Generazione dei Chunk per Embeddings
        chunks = chunk_legal_text(full_text, target_words=target_chunk_words, overlap_words=overlap_words)
        stats["total_chunks_created"] += len(chunks)

        total_chars = len(full_text)
        total_words = len(full_text.split())

        parsed_record = {
            "feedback_id": feedback_id,
            "organization": org_name,
            "user_type": user_type,
            "country": country,
            "raw_web_feedback": raw_web_feedback,
            "attachment_name": attachment_name,
            "has_attachment": has_attachment_decl,
            "has_attachment_file": has_attachment_file,
            "has_text": has_text,
            "full_text": full_text,
            "total_chars": total_chars,
            "total_words": total_words,
            "detected_articles": legal_refs["detected_articles"],
            "detected_annexes": legal_refs["detected_annexes"],
            "detected_recitals": legal_refs["detected_recitals"],
            "chunks": chunks
        }
        if extraction_error:
            parsed_record["extraction_warning"] = extraction_error

        parsed_records.append(parsed_record)

        if not TQDM_AVAILABLE and (idx % 10 == 0 or idx == len(records)):
            print(f"[{idx}/{len(records)}] Elaborato: '{org_name}' | Testo: {total_words} parole | Chunks: {len(chunks)}")

    # Salvataggio output
    with open(output_json_path, "w", encoding="utf-8") as f:
        json.dump(parsed_records, f, ensure_ascii=False, indent=2)

    print(f"\n🎉 Elaborazione completata con successo!")
    print(f"💾 File salvato in: '{output_json_path}'")
    print("\n" + "="*50)
    print("📊 STATISTICHE DI ELABORAZIONE:")
    print(f"  • Record totali elaborati:          {stats['total_records']}")
    print(f"  • Allegati trovati ed estratti:     {stats['attachments_found_on_disk']}")
    print(f"  • Record con testo valido:          {stats['records_with_extracted_text']}")
    print(f"  • Chunks totali generati:           {stats['total_chunks_created']}")
    print(f"  • Riferimenti Articoli estratti:    {stats['total_articles_detected']}")
    print(f"  • Riferimenti Allegati estratti:    {stats['total_annexes_detected']}")
    print("="*50 + "\n")

    return stats


# =====================================================================
# 7. CLI ENTRYPOINT
# =====================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Estrae, pulisce e suddivide in chunk i feedback e gli allegati PDF/DOCX delle lobby dell'AI Act."
    )
    parser.add_argument(
        "--input", "-i",
        default="ai_act_lobby_feedback.json",
        help="Percorso del file JSON di input scaricato (default: ai_act_lobby_feedback.json)"
    )
    parser.add_argument(
        "--attachments", "-a",
        default="attachments",
        help="Cartella contenente i file PDF/DOCX allegati (default: attachments/)"
    )
    parser.add_argument(
        "--output", "-o",
        default="parsed_lobby_contributions.json",
        help="Percorso del file JSON di output (default: parsed_lobby_contributions.json)"
    )
    parser.add_argument(
        "--chunk-size", "-c",
        type=int,
        default=400,
        help="Dimensione target dei chunk in numero di parole (default: 400)"
    )
    parser.add_argument(
        "--chunk-overlap",
        type=int,
        default=50,
        help="Overlap tra chunk adiacenti in numero di parole (default: 50)"
    )

    args = parser.parse_args()

    # Risoluzione percorsi relativi alla cartella corrente o alla cartella dello script
    script_dir = os.path.dirname(os.path.abspath(__file__))
    input_path = args.input if os.path.exists(args.input) else os.path.join(script_dir, args.input)
    attachments_dir = args.attachments if os.path.exists(args.attachments) else os.path.join(script_dir, args.attachments)
    output_path = args.output if os.path.isabs(args.output) else os.path.join(script_dir, args.output)

    process_lobby_feedbacks(
        input_json_path=input_path,
        attachments_dir=attachments_dir,
        output_json_path=output_path,
        target_chunk_words=args.chunk_size,
        overlap_words=args.chunk_overlap
    )


if __name__ == "__main__":
    main()
