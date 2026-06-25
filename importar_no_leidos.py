#!/usr/bin/env python3
"""
importar_no_leidos.py — Procesa todos los mails desde el 22/06 (leídos y no leídos).
Usa Claude para clasificar si es un CV para el lodge o no.
A prueba de balas: cada mail se procesa de forma independiente, errores no frenan el run.
"""
import sys, imaplib, email as emaillib, os, time, json, re, datetime
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
    ensure_positions, upload_pdf, create_candidate, link_couple,
)

ATTACHMENT_EXTS = (".pdf", ".doc", ".docx")
SINCE_DATE = datetime.date(2026, 6, 22)
IMAP_TIMEOUT = 30

_anthropic_client = None
def get_anthropic():
    global _anthropic_client
    if _anthropic_client is None:
        _anthropic_client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    return _anthropic_client


def decode_str(v):
    if not v: return ""
    parts = decode_header(v)
    return "".join(p.decode(e or "utf-8", errors="replace") if isinstance(p, bytes) else str(p) for p, e in parts)


def get_body(msg):
    body = ""
    try:
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
    except Exception:
        pass
    return body.strip()


def get_attachments(msg):
    atts = []
    try:
        for part in msg.walk():
            fn = part.get_filename()
            if not fn: continue
            fn = decode_str(fn)
            if any(fn.lower().endswith(ext) for ext in ATTACHMENT_EXTS):
                p = part.get_payload(decode=True)
                if p: atts.append({"filename": fn, "bytes": p})
    except Exception:
        pass
    return atts


def is_cv_email(sender, subject, body) -> bool:
    prompt = f"""Sos asistente de RRHH de Arroyo Pescado Lodge, un lodge de pesca de lujo en la Patagonia argentina.
Estamos buscando candidatos para dos puestos: Chef y Host/Hostess para la temporada nov 2026 - abr 2027.

Analizá este mail y respondé SOLO con "SI" si es una postulación laboral, envío de CV o aplicación a un puesto de trabajo.
Respondé "NO" si es cualquier otra cosa (factura, reserva, comunicado, publicidad, etc).

De: {sender}
Asunto: {subject}
Cuerpo: {body[:500]}

¿Es una postulación o CV?"""
    for attempt in range(3):
        try:
            resp = get_anthropic().messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=5,
                messages=[{"role": "user", "content": prompt}],
            )
            return resp.content[0].text.strip().upper().startswith("S")
        except Exception as e:
            if attempt == 2:
                print(f"[clasificador] Error tras 3 intentos: {e}")
                return False
            time.sleep(2)
    return False


def connect_imap(user, app_pass):
    mail = imaplib.IMAP4_SSL("imap.gmail.com", 993)
    mail.socket().settimeout(IMAP_TIMEOUT)
    mail.login(user, app_pass)
    mail.select("INBOX")
    return mail


def process_email(num, mail, user, search_id, positions, processed_ids):
    """Procesa un mail. Retorna (uid, candidate_ids) o (uid, []) si no se importó."""
    # UID
    try:
        _, uid_data = mail.fetch(num, "(UID)")
        uid_match = re.search(r"UID (\d+)", uid_data[0].decode())
        uid = uid_match.group(1) if uid_match else num.decode()
    except Exception:
        uid = num.decode()

    if uid in processed_ids:
        return uid, None  # None = ya procesado, saltar silenciosamente

    try:
        _, data = mail.fetch(num, "(BODY.PEEK[])")
        if not data or not isinstance(data[0], tuple):
            return uid, []
        msg = emaillib.message_from_bytes(data[0][1])
    except Exception as e:
        print(f"  [error fetch] {e}")
        return uid, []

    try:
        sender_name, sender_email = parseaddr(msg.get("From", ""))
        sender_name = decode_str(sender_name) or sender_email
        if sender_email.lower() == user.lower():
            return uid, None

        subject = decode_str(msg.get("Subject", ""))
        body = get_body(msg)
        atts = get_attachments(msg)

        if not atts:
            return uid, []

        print(f"  {sender_name[:30]} | {subject[:45]}", end=" ... ", flush=True)

        if not is_cv_email(sender_name, subject, body):
            print("NO es CV")
            return uid, []

        print("ES CV")

        # Extraer y procesar adjuntos
        atts_data = []
        for att in atts[:2]:
            try:
                cv_text = extract_attachment_text(att["filename"], att["bytes"])
                att_info = extract_name_and_position(cv_text, body if not atts_data else "", sender_name)
                atts_data.append({"att": att, "cv_text": cv_text, "name": att_info["full_name"], "pos": att_info["position"]})
            except Exception as e:
                print(f"  [error extracción] {e}")

        if not atts_data:
            return uid, []

        # Mismo nombre = CV + carta
        actually_couple = False
        if len(atts_data) == 2:
            if atts_data[0]["name"].strip().lower() == atts_data[1]["name"].strip().lower():
                atts_data = [atts_data[0] if len(atts_data[0]["cv_text"]) >= len(atts_data[1]["cv_text"]) else atts_data[1]]
            else:
                actually_couple = True

        pos_cfg = next((p for p in positions if p["title"] == atts_data[0]["pos"]), positions[0])
        candidate_ids = []

        for i, ad in enumerate(atts_data):
            try:
                final_pos = next((p for p in positions if p["title"] == ad["pos"]), pos_cfg)
                partner_name = atts_data[1-i]["name"] if actually_couple else ""
                partner_cv   = atts_data[1-i]["cv_text"] if actually_couple else ""

                match = match_cv(
                    cv_text=ad["cv_text"], bio=body if i == 0 else "",
                    candidate_name=ad["name"],
                    position_title=final_pos["title"],
                    position_requirements=final_pos["requirements"],
                    is_couple=actually_couple,
                    partner_name=partner_name, partner_cv_text=partner_cv,
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
                print(f"  → {ad['name']} ({final_pos['title']}) score {match['score']}")
            except Exception as e:
                print(f"  [error importando {ad['name']}] {e}")

        if len(candidate_ids) == 2:
            try:
                link_couple(candidate_ids[0], candidate_ids[1])
            except Exception as e:
                print(f"  [error vinculando pareja] {e}")

        return uid, candidate_ids

    except Exception as e:
        print(f"  [error general] {e}")
        return uid, []


def main():
    cfg = yaml.safe_load((root / "config.yaml").read_text(encoding="utf-8"))
    search_id = get_or_create_search(cfg["search"]["name"], cfg["search"]["company"])
    positions = ensure_positions(search_id, cfg["positions"])
    processed_ids = get_processed_message_ids(search_id)

    user     = os.environ["GMAIL_USER"]
    app_pass = os.environ["GMAIL_APP_PASS"]

    mail = connect_imap(user, app_pass)
    since_str = SINCE_DATE.strftime("%d-%b-%Y")
    _, nums = mail.search(None, f'SINCE "{since_str}"')
    ids = nums[0].split() if nums[0] else []
    print(f"{len(ids)} mails desde {since_str} (leídos y no leídos)\n")

    imported = 0
    imported_uids = []

    for i, num in enumerate(ids, 1):
        # Reconectar IMAP si cayó
        try:
            mail.noop()
        except Exception:
            try:
                mail = connect_imap(user, app_pass)
            except Exception as e:
                print(f"[IMAP] No se pudo reconectar: {e}")
                time.sleep(5)
                continue

        print(f"[{i}/{len(ids)}]", end=" ")
        uid, candidate_ids = process_email(num, mail, user, search_id, positions, processed_ids)

        if candidate_ids is None:
            print(f"  (ya procesado)")
            continue
        if candidate_ids:
            imported += len(candidate_ids)
            imported_uids.append(uid)
            processed_ids.add(uid)

        time.sleep(0.2)

    # Marcar como leídos
    if imported_uids:
        try:
            mail.uid("STORE", ",".join(imported_uids), "+FLAGS", "\\Seen")
            print(f"\n{len(imported_uids)} mails marcados como leídos.")
        except Exception as e:
            print(f"\nError marcando leídos: {e}")

    try:
        mail.logout()
    except Exception:
        pass

    print(f"\n{'='*50}")
    print(f"Candidatos importados: {imported}")


if __name__ == "__main__":
    main()
