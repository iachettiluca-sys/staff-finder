#!/usr/bin/env python3
"""
run_scraper.py — Scraper principal de CVs.
Corre cada 2 días vía GitHub Actions o manualmente.
"""
from __future__ import annotations
import sys, os, smtplib, ssl
from pathlib import Path
from email.message import EmailMessage

sys.path.insert(0, str(Path(__file__).parent / "src"))

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env")

import yaml
from gmail_scraper import scrape_gmail
from pdf_extractor import extract_attachment_text
from cv_matcher import match_cv
from name_extractor import extract_name_and_position
from supabase_ops import (
    get_or_create_search, get_processed_message_ids,
    ensure_positions, upload_pdf, create_candidate, link_couple,
)


def send_email(subject: str, body: str) -> None:
    host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    port = int(os.environ.get("SMTP_PORT", "587"))
    user = os.environ.get("SMTP_USER")
    pwd  = os.environ.get("SMTP_PASS")
    to   = os.environ.get("NOTIFY_TO", user)
    if not (user and pwd and to):
        print(f"[notify] Sin SMTP configurado. Mensaje:\n{body}")
        return
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = user
    msg["To"] = to
    msg.set_content(body)
    ctx = ssl.create_default_context()
    with smtplib.SMTP(host, port) as s:
        s.starttls(context=ctx)
        s.login(user, pwd)
        s.send_message(msg)


def find_position(subject: str, body: str, positions: list[dict]) -> dict | None:
    text = (subject + " " + body[:1000]).lower()
    for pos in positions:
        if pos["title"].lower() in text:
            return pos
    return positions[0] if positions else None


def main() -> int:
    cfg_path = Path(__file__).parent / "config.yaml"
    cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))

    search_cfg = cfg["search"]
    positions_cfg = cfg["positions"]
    couple_keywords = cfg.get("couple_keywords", [])

    # Setup Supabase
    search_id = get_or_create_search(search_cfg["name"], search_cfg["company"])
    print(f"[scraper] Search ID: {search_id}")

    positions = ensure_positions(search_id, positions_cfg)
    processed_ids = get_processed_message_ids(search_id)
    print(f"[scraper] Ya procesados: {len(processed_ids)} candidatos")

    # Scrape Gmail
    emails = scrape_gmail(search_cfg["gmail_since"], couple_keywords, processed_ids)
    print(f"[scraper] Nuevos mails con CV: {len(emails)}")

    imported = 0
    couple_questions = []

    for mail_data in emails:
        message_id = mail_data["message_id"]
        sender_name = mail_data["sender_name"]
        sender_email = mail_data["sender_email"]
        body = mail_data["body"]
        is_couple = mail_data["is_couple"]
        attachments = mail_data["attachments"]

        # Extraer nombre real y puesto desde el primer CV adjunto
        first_cv_text = ""
        if attachments:
            first_cv_text = extract_attachment_text(attachments[0]["filename"], attachments[0]["bytes"])
        info = extract_name_and_position(first_cv_text, body, sender_name)
        sender_name = info["full_name"]
        position_detected = info["position"]

        # Find position config
        position = find_position(mail_data["subject"], body, positions)
        if position_detected != "unknown":
            position = next((p for p in positions if p["title"] == position_detected), position)
        pos_title = position["title"] if position else position_detected
        pos_requirements = position["requirements"] if position else ""

        if is_couple and len(attachments) == 1:
            # Can't auto-split couple — ask Lucas
            couple_questions.append(
                f"Mail de: {sender_name} ({sender_email})\n"
                f"Asunto: {mail_data['subject']}\n"
                f"Mencionan ser pareja pero solo adjuntaron 1 CV. "
                f"¿Cómo los cargo? (uno o dos candidatos separados)"
            )

        if not attachments:
            # No attachment — create minimal candidate with bio only
            pdf_url = ""
            cv_text = ""
            match = match_cv(cv_text, body, sender_name, pos_title, pos_requirements)
            candidate_id = create_candidate({
                "search_id": search_id,
                "name": sender_name,
                "email": sender_email,
                "bio": body,
                "pdf_url": pdf_url,
                "pdf_text": cv_text,
                "gmail_message_id": message_id,
                "position": pos_title,
                "category": "couple" if is_couple else "solo",
                "status": "nuevo",
                "ai_score": match["score"],
                "ai_summary": match["summary"],
                "ai_strengths": match["strengths"],
                "ai_gaps": match["gaps"],
            })
            imported += 1
            print(f"[scraper] Candidato importado (sin CV): {sender_name} — score: {match['score']}")
            continue

        # Extract name and text from each attachment first (max 2)
        atts = attachments[:2]
        att_data = []
        for att in atts:
            cv_text = extract_attachment_text(att["filename"], att["bytes"])
            att_info = extract_name_and_position(cv_text, body if not att_data else "", sender_name)
            att_data.append({"att": att, "cv_text": cv_text,
                             "name": att_info["full_name"],
                             "pos": att_info["position"]})

        # Mismo nombre en los dos adjuntos → CV + carta, no pareja
        if (len(att_data) == 2 and
                att_data[0]["name"].strip().lower() == att_data[1]["name"].strip().lower()):
            print(f"[scraper] Mismo nombre ('{att_data[0]['name']}') en 2 adjuntos — no es pareja, tomando el de mayor contenido")
            att_data = [att_data[0] if len(att_data[0]["cv_text"]) >= len(att_data[1]["cv_text"]) else att_data[1]]
            is_couple = False

        actually_couple = is_couple and len(att_data) == 2
        candidate_ids = []
        for i, ad in enumerate(att_data):
            pdf_url = upload_pdf(search_id, f"{message_id}_{i}_{ad['att']['filename']}", ad["att"]["bytes"])

            partner_name = att_data[1 - i]["name"] if actually_couple else ""
            partner_cv   = att_data[1 - i]["cv_text"] if actually_couple else ""

            # Override position with Claude-detected if not unknown
            final_pos = position
            if ad["pos"] != "unknown":
                final_pos = next((p for p in positions if p["title"] == ad["pos"]), position)

            match = match_cv(
                cv_text=ad["cv_text"],
                bio=body if i == 0 else "",
                candidate_name=ad["name"],
                position_title=final_pos["title"] if final_pos else pos_title,
                position_requirements=final_pos["requirements"] if final_pos else pos_requirements,
                is_couple=actually_couple,
                partner_name=partner_name,
                partner_cv_text=partner_cv,
            )

            candidate_id = create_candidate({
                "search_id": search_id,
                "name": ad["name"],
                "email": sender_email if i == 0 else "",
                "bio": body if i == 0 else "",
                "pdf_url": pdf_url,
                "pdf_text": ad["cv_text"],
                "gmail_message_id": message_id if i == 0 else f"{message_id}_p2",
                "position": final_pos["title"] if final_pos else pos_title,
                "category": "couple" if actually_couple else "solo",
                "status": "nuevo",
                "ai_score": match["score"],
                "ai_summary": match["summary"],
                "ai_strengths": match["strengths"],
                "ai_gaps": match["gaps"],
            })
            candidate_ids.append(candidate_id)
            imported += 1
            print(f"[scraper] Candidato importado: {ad['name']} — score: {match['score']}")

        if len(candidate_ids) == 2:
            link_couple(candidate_ids[0], candidate_ids[1])
            print(f"[scraper] Pareja vinculada: {candidate_ids[0]} <-> {candidate_ids[1]}")

    # Notify about couple questions
    if couple_questions:
        body_q = "Hay candidatos que se presentaron como pareja pero con un solo CV adjunto. Necesito saber cómo cargarlos:\n\n"
        body_q += "\n\n---\n\n".join(couple_questions)
        send_email("Staff Finder — Consulta sobre parejas", body_q)
        print(f"[scraper] Consulta de pareja enviada por mail ({len(couple_questions)} casos)")

    # Summary email
    if imported > 0:
        send_email(
            f"Staff Finder — {imported} candidato(s) nuevo(s) importado(s)",
            f"Se importaron {imported} candidato(s) nuevos al Staff Finder.\n\n"
            f"Búsqueda: {search_cfg['name']}\n"
            f"Empresa: {search_cfg['company']}\n\n"
            f"Revisalos en la app.",
        )

    print(f"\n[scraper] Listo. Importados: {imported}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
