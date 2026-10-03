import urllib.request
import urllib.parse
import json
import time
import os
import re

PUBLICATION_ID = 13340
BASE_URL = "https://ec.europa.eu/info/law/better-regulation/api/allFeedback"
DOWNLOAD_URL_TEMPLATE = "https://ec.europa.eu/info/law/better-regulation/api/download/{document_id}"

OUTPUT_FILE = "ai_act_lobby_feedback.json"
ATTACHMENTS_DIR = "attachments"

# Soglia sotto la quale il commento è considerato "troppo corto" (o se rimanda all'allegato)
SHORT_COMMENT_THRESHOLD = 200

# Filtro Lobbisti: consideriamo solo le organizzazioni con effettivo interesse di lobbying
TARGET_USER_TYPES = {
    "COMPANY",                          # Grandi aziende (Google, Microsoft, ecc.)
    "BUSINESS_ASSOCIATION",             # Federazioni di settore (DIGITALEUROPE, CCIA, ecc.)
    "NON_GOVERNMENTAL_ORGANIZATION",    # ONG (EDRi, Amnesty, ecc.)
    "TRADE_UNION"                       # Sindacati dei lavoratori
}

HEADERS = {
    'Accept': 'application/json, text/plain, */*',
    'Accept-Language': 'it-IT,it;q=0.9,en-US;q=0.8,en;q=0.7',
    'Cache-Control': 'No-Cache',
    'Connection': 'keep-alive',
    'Referer': 'https://ec.europa.eu/info/law/better-regulation/have-your-say/initiatives/12527-Artificial-intelligence-ethical-and-legal-requirements/feedback_en_en?p_id=13340',
    'Sec-Fetch-Dest': 'empty',
    'Sec-Fetch-Mode': 'cors',
    'Sec-Fetch-Site': 'same-origin',
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36',
    'X-Requested-With': 'XMLHttpRequest',
    'sec-ch-ua': '"Chromium";v="152", "Not?A_Brand";v="24", "Google Chrome";v="152"',
    'sec-ch-ua-mobile': '?0',
    'sec-ch-ua-platform': '"macOS"',
    'Cookie': 'cck1=%7B%22cm%22%3Atrue%2C%22all1st%22%3Atrue%7D; _pcid=%7B%22browserId%22%3A%22muri8ry0l9ke34hg%22%2C%22_t%22%3A%22nafzbf26%7Cmuri8ry6%22%7D; _pctx=%7Bu%7DN4IgrgzgpgThIC4B2YA2qA05owMoBcBDfSREQpAeyRCwgEt8oBJAE0RXSwH18ylCAMwBeAI0EAmAOwAfALZgY9ABwwAnlJABfIA; _pprv=eyJjb25zZW50Ijp7IjAiOnsibW9kZSI6Im9wdC1pbiJ9LCI3Ijp7Im1vZGUiOiJvcHQtaW4ifX0sInB1cnBvc2VzIjp7IjAiOiJBTSIsIjciOiJETCJ9LCJfdCI6Im5hZnpiZjIyfG11cmk4cnkyIn0%3D; dtCookie=v_4_srv_56_sn_30E33035B60ABF6B78ABD5A2A000EE02_perc_100000_ol_0_mul_1_app-3A47d4c64c3b67ec69_0_app-3A9daca62c888adcd6_1_rcs-3Acss_0'
}

def sanitize_filename(filename: str) -> str:
    """Rimuove caratteri vietati nei filesystem per evitare errori di salvataggio."""
    return re.sub(r'[\\/*?:"<>|]', '_', filename).strip()

def is_comment_too_short(text: str) -> bool:
    """True se il commento ha pochi caratteri o dice 'vedi allegato'."""
    if not text or len(text.strip()) < SHORT_COMMENT_THRESHOLD:
        return True
    triggers = ["attached", "attachment", "allegat", "see file", "in the file", "position paper", "submission"]
    text_lower = text.lower()
    return any(t in text_lower for t in triggers)

def download_attachment_file(url: str, output_path: str) -> bool:
    """Scarica il file allegato (PDF/DOCX) mantenendo la sessione."""
    if os.path.exists(output_path) and os.path.getsize(output_path) > 1000:
        return True  # Già scaricato
    
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            content = resp.read()
            with open(output_path, "wb") as f:
                f.write(content)
        return True
    except Exception as e:
        print(f"      ⚠️ Errore download allegato ({url}): {e}")
        return False

def extract_text_from_file(file_path: str) -> str:
    """Estrae testo da PDF o DOCX se disponibili."""
    if not os.path.exists(file_path):
        return ""

    ext = os.path.splitext(file_path)[1].lower()

    if ext == ".docx":
        try:
            import docx
            doc = docx.Document(file_path)
            return "\n".join(p.text for p in doc.paragraphs if p.text.strip())
        except Exception:
            return ""

    if ext == ".pdf":
        try:
            import pypdf
            reader = pypdf.PdfReader(file_path)
            pages = [page.extract_text() or "" for page in reader.pages]
            return "\n".join(pages).strip()
        except ImportError:
            pass
        except Exception:
            pass

        try:
            import pdfplumber
            with pdfplumber.open(file_path) as pdf:
                pages = [page.extract_text() or "" for page in pdf.pages]
                return "\n".join(pages).strip()
        except ImportError:
            pass
        except Exception:
            pass

    return ""

def download_all_feedback():
    all_filtered_feedback = []
    page = 0
    page_size = 25  # A blocchi di 25
    total_processed = 0

    print(f"🚀 Inizio download dei feedback per Publication ID: {PUBLICATION_ID}...")
    print(f"🎯 Filtro Lobbisti attivi: {TARGET_USER_TYPES}")
    print(f"📁 Cartella allegati: '{ATTACHMENTS_DIR}/'")

    while True:
        url = f"{BASE_URL}?publicationId={PUBLICATION_ID}&language=EN&page={page}&size={page_size}&sort=dateFeedback,DESC"
        req = urllib.request.Request(url, headers=HEADERS)

        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                if response.status != 200:
                    print(f"⚠️ Errore HTTP {response.status} a pagina {page}")
                    break

                data = json.loads(response.read().decode('utf-8'))
                
                # In Spring Boot la lista si chiama 'content' e il totale 'totalElements'
                items = data.get("content", [])
                total = data.get("totalElements", 0)
                is_last = data.get("last", False)

                if not items:
                    print(f"✅ Nessun elemento a pagina {page}.")
                    break

                total_processed += len(items)

                # FILTRO E PROCESSAMENTO PER OGNI ELEMENTO
                for item in items:
                    user_type = item.get("userType", "")

                    # 1. Filtra solo lobbisti di interesse
                    if user_type not in TARGET_USER_TYPES:
                        continue

                    raw_feedback_text = item.get("feedback", "") or ""
                    attachments = item.get("attachments", []) or []
                    attached_texts = []

                    needs_attachment = is_comment_too_short(raw_feedback_text) and len(attachments) > 0

                    # 2. Gestione Allegati: scarica nella cartella dedicata con nome identico al JSON
                    for att in attachments:
                        original_filename = att.get("fileName") or att.get("ersFileName") or f"attachment_{att.get('id')}.pdf"
                        safe_filename = sanitize_filename(original_filename)

                        # Salva nella cartella dedicata 'attachments/' mantenendo lo stesso nome
                        local_path = os.path.join(ATTACHMENTS_DIR, safe_filename)
                        document_id = att.get("documentId")

                        att["localFilePath"] = local_path
                        att["downloadUrl"] = DOWNLOAD_URL_TEMPLATE.format(document_id=document_id) if document_id else None

                        if needs_attachment and document_id:
                            print(f"   📥 Scarico allegato per '{item.get('organization', 'N/A')}': {safe_filename}")
                            download_ok = download_attachment_file(att["downloadUrl"], local_path)
                            if download_ok:
                                extracted_txt = extract_text_from_file(local_path)
                                if extracted_txt:
                                    att["extractedTextLength"] = len(extracted_txt)
                                    attached_texts.append(extracted_txt)

                    # 3. Costruzione del commento completo
                    full_comment_parts = []
                    if raw_feedback_text.strip():
                        full_comment_parts.append(raw_feedback_text.strip())
                    if attached_texts:
                        full_comment_parts.append("\n\n--- [TESTO ALLEGATO] ---\n\n" + "\n\n".join(attached_texts))

                    item["full_comment"] = "\n\n".join(full_comment_parts)
                    item["has_attachment_downloaded"] = len(attached_texts) > 0

                    all_filtered_feedback.append(item)

                print(f"📦 Pagina {page}: letti {total_processed}/{total} totali | Lobbisti target salvati: {len(all_filtered_feedback)}")

                if is_last or total_processed >= total:
                    print("🎉 Raggiunto l'ultimo elemento della consultazione!")
                    break

                page += 1
                time.sleep(0.4)

        except Exception as e:
            print(f"❌ Errore durante il download a pagina {page}: {e}")
            break

    # 4. Salvataggio del file finale
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(all_filtered_feedback, f, indent=2, ensure_ascii=False)

    print(f"\n✨ Operazione completata! Salvati {len(all_filtered_feedback)} feedback lobbisti in '{OUTPUT_FILE}'.")
    print(f"📁 Allegati memorizzati nella cartella: '{ATTACHMENTS_DIR}/'")

if __name__ == "__main__":
    download_all_feedback()
