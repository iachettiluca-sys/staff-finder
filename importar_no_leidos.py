#!/usr/bin/env python3
"""
importar_no_leidos.py — Procesa todos los mails NO LEÍDOS con adjunto PDF/DOCX.
Usa Claude para clasificar si es un CV para el lodge o no (sin keywords hardcodeadas).
Importa los que son CVs y los marca como leídos.
"""
import sys, imaplib, email as emaillib, os, time, json
from pathlib import Path
root = Path(__file__).resolve().parent
sys.path.insert(0, str(root / "src"))
from dotenv import load_dotenv; load_dotenv(root / ".env")

from email.header import decode_header
from email.utils import parseaddr
from bs4 import BeautifulSoup
import anthropic
import yaml
from pdf_extractor import extract_attachment_text
from name_extractor import extract_name_and_position
from cv_matcher import match_cv
from age_nationality_extractor import extract_age_nationality
from supabase_ops import (
    get_or_create_search, get_processed_message_ids,
    ensure_positions, upload_pdf, create_candidate, link_couple, get_client,
)

ATTACHMENT_EXTS = (".pdf", ".doc", ".docx")

_anthropic = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])


def decode_str(v):
    if not v: return ""
    parts = decode_header(v)
    return "".join(p.decode(e or "utf-8", errors="replace") if isinstance(p, bytes) else str(p) for p, e in parts)


def get_body(msg):
    body = ""
    for part in msg.walk():
        ct = part.get_content_type()
        if "attachment" in str(part.get("Content-Disposition", "")): continue
        if ct == "text/plain":
            p = part.get_payload(decode=True)
            if p: body += p.decode(part.get_content_charset() or "utf-8", errors="replace")
        elif ct == "text/html" and not body:
            p = part.get_payload(decode=True)
            if p:
                body += BeautifulSoup(p.decode(part.get_content_charset() or "utf-8", errors="replace"), "html.parser").get_text()
    return body.strip()


def get_attachments(msg):
    atts = []
    for part in msg.walk():
        fn = part.get_filename()
        if not fn: continue
        fn = decode_str(fn)
        if any(fn.lower().endswith(ext) for ext in ATTACHMENT_EXTS):
            p = part.get_payload(decode=True)
            if p: atts.append({"filename": fn, "bytes": p})
    return atts


def is_cv_email(sender, subject, body) -> bool:
    """Claude clasifica si el mail es una postulación laboral para el lodge."""
    prompt = f"""Sos asistente de RRHH de un lodge de pesca de lujo en la Patagonia argentina (Arroyo Pescado Lodge).
Estamos buscando candidatos para dos puestos: Chef y Host/Hostess.

Analizá este mail y respondé SOLO con "SI" si es una postulación laboral o envío de CV, o "NO" si no lo es.

De: {sender}
Asunto: {subject}
Cuerpo (primeros 400 caracteres): {body[:400]}

¿Es una postulación laboral o CV?"""

    try:
        resp = _anthropic.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=5,
            messages=[{"role": "user", "content": prompt}],
        )
        answer = resp.content[0].text.strip().upper()
        return answer.startswith("S")
    except Exception as e:
        print(f"[clasificador] Error: {e}")
        return False


def main():
    cfg = yaml.safe_load((root / "config.yaml").read_text(encoding="utf-8"))
    search_cfg = cfg["search"]
    positions_cfg = cfg["positions"]
    couple_keywords = cfg.get("couple_keywords", [])

    search_id = get_or_create_search(search_cfg["name"], search_cfg["company"])
    positions = ensure_positions(search_id, positions_cfg)
    pos_map = {p["title"]: p for p in positions}
    processed_ids = get_processed_message_ids(search_id)

    user     = os.environ["GMAIL_USER"]
    app_pass = os.environ["GMAIL_APP_PASS"]

    mail = imaplib.IMAP4_SSL("imap.gmail.com", 993)
    mail.login(user, app_pass)
    mail.select("INBOX")

    # Buscar TODOS los no leídos
    _, nums = mail.search(None, "UNSEEN")
    ids = nums[0].split() if nums[0] else []
    print(f"{len(ids)} mails no leídos en INBOX\n")

    imported = 0
    skipped_noatt = 0
    skipped_nocv = 0
    imported_uids = []

    for num in ids:
        # Obtener UID
        import re
        _, uid_data = mail.fetch(num, "(UID)")
        uid_match = re.search(r"UID (\d+)", uid_data[0].decode())
        uid = uid_match.group(1) if uid_match else num.decode()

        if uid in processed_ids:
            continue

        _, data = mail.fetch(num, "(BODY.PEEK[])")
        if not data or not isinstance(data[0], tuple): continue
        msg = emaillib.message_from_bytes(data[0][1])

        sender_name, sender_email = parseaddr(msg.get("From", ""))
        sender_name = decode_str(sender_name) or sender_email
        if sender_email.lower() == user.lower(): continue

        subject = decode_str(msg.get("Subject", ""))
        body = get_body(msg)
        atts = get_attachments(msg)

        if not atts:
            skipped_noatt += 1
            continue

        print(f"→ {sender_name} | {subject[:50]}", end=" ... ", flush=True)

        # Claude clasifica
        if not is_cv_email(sender_name, subject, body):
            print("NO es CV")
            skipped_nocv += 1
            continue

        print("ES CV — importando")

        # Importar (misma lógica que run_scraper.py)
        first_cv_text = extract_attachment_text(atts[0]["filename"], atts[0]["bytes"])
        info = extract_name_and_position(first_cv_text, body, sender_name)
        name = info["full_name"]
        position_detected = info["position"]

        pos_cfg = next((p for p in positions if p["title"] == position_detected), positions[0])

        atts_data = []
        for att in atts[:2]:
            cv_text = extract_attachment_text(att["filename"], att["bytes"])
            att_info = extract_name_and_position(cv_text, body if not atts_data else "", sender_name)
            atts_data.append({"att": att, "cv_text": cv_text, "name": att_info["full_name"], "pos": att_info["position"]})

        # Mismo nombre = CV + carta, no pareja
        actually_couple = False
        if len(atts_data) == 2:
            if atts_data[0]["name"].strip().lower() == atts_data[1]["name"].strip().lower():
                atts_data = [atts_data[0] if len(atts_data[0]["cv_text"]) >= len(atts_data[1]["cv_text"]) else atts_data[1]]
            else:
                actually_couple = True

        candidate_ids = []
        for i, ad in enumerate(atts_data):
            final_pos = next((p for p in positions if p["title"] == ad["pos"]), pos_cfg)
            partner_name = atts_data[1-i]["name"] if actually_couple else ""
            partner_cv   = atts_data[1-i]["cv_text"] if actually_couple else ""

            match = match_cv(
                cv_text=ad["cv_text"], bio=body if i == 0 else "",
                candidate_name=ad["name"],
                position_title=final_pos["title"],
                position_requirements=final_pos["requirements"],
                is_couple=actually_couple,
                partner_name=partner_name,
                partner_cv_text=partner_cv,
            )
            profile = extract_age_nationality(ad["cv_text"], body if i == 0 else "")
            pdf_url = upload_pdf(search_id, f"{uid}_{i}_{ad['att']['filename']}", ad["att"]["bytes"])

            cid = create_candidate({
                "search_id": search_id,
                "name": ad["name"],
                "email": sender_email if i == 0 else "",
                "bio": body if i == 0 else "",
                "pdf_url": pdf_url,
                "pdf_text": ad["cv_text"],
                "gmail_message_id": uid if i == 0 else f"{uid}_p2",
                "position": final_pos["title"],
                "category": "couple" if actually_couple else "solo",
                "status": "nuevo",
                "ai_score": match["score"],
                "ai_summary": match["summary"],
                "ai_strengths": match["strengths"],
                "ai_gaps": match["gaps"],
                "age": profile.get("age"),
                "nationality": profile.get("nationality"),
            })
            candidate_ids.append(cid)
            imported += 1
            print(f"  [{ad['name']}] score {match['score']}")

        if len(candidate_ids) == 2:
            link_couple(candidate_ids[0], candidate_ids[1])

        if candidate_ids:
            imported_uids.append(uid)

        time.sleep(0.3)

    # Marcar como leídos
    if imported_uids:
        uid_str = ",".join(imported_uids)
        try:
            mail.uid("STORE", uid_str, "+FLAGS", "\\Seen")
            print(f"\n{len(imported_uids)} mails marcados como leídos.")
        except Exception as e:
            print(f"\nError marcando como leídos: {e}")

    mail.logout()

    print(f"\n{'='*50}")
    print(f"Importados:        {imported} candidatos")
    print(f"Sin adjunto:       {skipped_noatt}")
    print(f"No son CVs:        {skipped_nocv}")


if __name__ == "__main__":
    main()
