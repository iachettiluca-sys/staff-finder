#!/usr/bin/env python3
"""
importar_parejas_faltantes.py — Re-procesa emails específicos para importar las parejas que faltaron.
"""
import sys, imaplib, email as emaillib, re, datetime, os
from pathlib import Path
root = Path(__file__).resolve().parent
sys.path.insert(0, str(root / "src"))
from dotenv import load_dotenv; load_dotenv(root / ".env")

from email.header import decode_header
from email.utils import parseaddr
import yaml
from pdf_extractor import extract_attachment_text
from name_extractor import extract_name_and_position
from cv_matcher import match_cv
from age_nationality_extractor import extract_age_nationality
from supabase_ops import (
    get_or_create_search, ensure_positions, upload_pdf,
    create_candidate, link_couple, get_client, find_candidate_by_name,
)

ATTACHMENT_EXTS = (".pdf", ".doc", ".docx")

# Emails a re-procesar
TARGET_SENDERS = [
    "lucasgrellet98@gmail.com",    # Lucas Grellet + Meline Cabeau
    "anastasia.wong@me.com",       # Joseph Kellow + Anastasia Wong
    "clagos425@gmail.com",         # Cristina Lagos + Gabriel Smith
]


def decode_str(value) -> str:
    if not value:
        return ""
    parts = decode_header(value)
    result = []
    for part, enc in parts:
        if isinstance(part, bytes):
            result.append(part.decode(enc or "utf-8", errors="replace"))
        else:
            result.append(str(part))
    return "".join(result)


def get_attachments(msg) -> list[dict]:
    atts = []
    for part in msg.walk():
        filename = part.get_filename()
        if not filename:
            continue
        filename = decode_str(filename)
        if not any(filename.lower().endswith(ext) for ext in ATTACHMENT_EXTS):
            continue
        payload = part.get_payload(decode=True)
        if payload:
            atts.append({"filename": filename, "bytes": payload})
    return atts


def get_body_text(msg) -> str:
    from bs4 import BeautifulSoup
    body = ""
    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            cd = str(part.get("Content-Disposition", ""))
            if "attachment" in cd:
                continue
            if ct == "text/plain":
                payload = part.get_payload(decode=True)
                if payload:
                    body += payload.decode(part.get_content_charset() or "utf-8", errors="replace")
            elif ct == "text/html" and not body:
                payload = part.get_payload(decode=True)
                if payload:
                    html = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
                    body += BeautifulSoup(html, "html.parser").get_text(separator="\n")
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            text = payload.decode(msg.get_content_charset() or "utf-8", errors="replace")
            body = text
    return body.strip()


def process_couple_email(msg, body, atts, search_id, positions, sb):
    """Procesa un email con 2+ adjuntos, importa los que faltan y vincula la pareja."""
    att_data = []
    for att in atts[:3]:
        cv_text = extract_attachment_text(att["filename"], att["bytes"])
        info = extract_name_and_position(cv_text, body if not att_data else "", att["filename"])
        att_data.append({"att": att, "cv_text": cv_text, "name": info["full_name"], "pos": info["position"]})

    # Deduplicar por nombre
    seen_names = []
    unique_att_data = []
    for ad in att_data:
        name_lower = ad["name"].strip().lower()
        if name_lower not in seen_names and not any(n in name_lower for n in seen_names):
            seen_names.append(name_lower)
            unique_att_data.append(ad)

    if len(unique_att_data) < 2:
        print(f"  Solo se detectó una persona única: {[a['name'] for a in unique_att_data]}")
        return

    ad1, ad2 = unique_att_data[0], unique_att_data[1]
    print(f"  Personas: {ad1['name']} + {ad2['name']}")

    # Ver cuáles ya están en la DB
    existing1 = find_candidate_by_name(search_id, ad1["name"])
    existing2 = find_candidate_by_name(search_id, ad2["name"])

    ids = []

    for i, (ad, existing) in enumerate([(ad1, existing1), (ad2, existing2)]):
        if existing:
            print(f"  [ya existe] {ad['name']} (id={existing['id'][:8]})")
            ids.append(existing["id"])
        else:
            pos_cfg = next((p for p in positions if p["title"] == ad["pos"]), positions[0])
            partner_ad = ad2 if i == 0 else ad1
            match = match_cv(
                cv_text=ad["cv_text"], bio=body if i == 0 else "",
                candidate_name=ad["name"],
                position_title=pos_cfg["title"],
                position_requirements=pos_cfg["requirements"],
                is_couple=True,
                partner_name=partner_ad["name"],
                partner_cv_text=partner_ad["cv_text"],
            )
            profile = extract_age_nationality(ad["cv_text"], body if i == 0 else "")
            pdf_url = upload_pdf(search_id, f"fix_{ad['att']['filename']}", ad["att"]["bytes"])
            cid = create_candidate({
                "search_id": search_id,
                "name": ad["name"],
                "email": "",
                "bio": body if i == 0 else "",
                "pdf_url": pdf_url,
                "pdf_text": ad["cv_text"],
                "gmail_message_id": f"fix_{ad['name'].replace(' ', '_')}",
                "position": pos_cfg["title"],
                "category": "couple",
                "status": "nuevo",
                "ai_score": match["score"],
                "ai_summary": match["summary"],
                "ai_strengths": match["strengths"],
                "ai_gaps": match["gaps"],
                "age": profile.get("age"),
                "nationality": profile.get("nationality"),
            })
            ids.append(cid)
            print(f"  [importado] {ad['name']} — score {match['score']}")

    if len(ids) == 2:
        # Verificar si ya están vinculados
        r1 = sb.table("candidates").select("couple_partner_id").eq("id", ids[0]).execute()
        already_linked = r1.data and r1.data[0].get("couple_partner_id") == ids[1]
        if not already_linked:
            link_couple(ids[0], ids[1])
            print(f"  [vinculados] {ad1['name']} <-> {ad2['name']}")
        else:
            print(f"  [ya vinculados] {ad1['name']} <-> {ad2['name']}")


def main():
    cfg = yaml.safe_load((root / "config.yaml").read_text(encoding="utf-8"))
    search_cfg = cfg["search"]
    positions_cfg = cfg["positions"]

    search_id = get_or_create_search(search_cfg["name"], search_cfg["company"])
    positions = ensure_positions(search_id, positions_cfg)
    sb = get_client()

    user     = os.environ.get("GMAIL_USER")
    app_pass = os.environ.get("GMAIL_APP_PASS")
    if not user or not app_pass:
        print("ERROR: GMAIL_USER o GMAIL_APP_PASS no configurados")
        sys.exit(1)

    dt = datetime.date.fromisoformat("2026-06-01")
    since_str = dt.strftime("%d-%b-%Y")

    mail = imaplib.IMAP4_SSL("imap.gmail.com", 993)
    mail.login(user, app_pass)
    mail.select("INBOX")

    for sender_email in TARGET_SENDERS:
        print(f"\n=== Buscando mail de {sender_email} ===")
        _, msg_nums = mail.search(None, f'SINCE "{since_str}" FROM "{sender_email}"')
        ids = msg_nums[0].split() if msg_nums[0] else []

        if not ids:
            print(f"  No se encontraron mails.")
            continue

        # Tomar el más reciente
        num = ids[-1]
        _, data = mail.fetch(num, "(BODY.PEEK[])")
        if not data or not isinstance(data[0], tuple):
            continue

        raw = data[0][1]
        msg = emaillib.message_from_bytes(raw)
        body = get_body_text(msg)
        atts = get_attachments(msg)

        print(f"  Adjuntos: {[a['filename'] for a in atts]}")

        if len(atts) < 2:
            print(f"  Solo tiene {len(atts)} adjunto(s) — no es pareja.")
            continue

        process_couple_email(msg, body, atts, search_id, positions, sb)

    mail.logout()
    print("\nListo.")


if __name__ == "__main__":
    main()
