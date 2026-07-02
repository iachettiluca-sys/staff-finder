#!/usr/bin/env python3
"""
run_scraper.py — Scraper principal de CVs.
Corre cada 2 días vía GitHub Actions o manualmente.
"""
from __future__ import annotations
import sys, os, smtplib, ssl
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from pathlib import Path
from email.message import EmailMessage

sys.path.insert(0, str(Path(__file__).parent / "src"))

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env")

import yaml
from gmail_scraper import scrape_gmail, mark_messages_seen
from pdf_extractor import extract_attachment_text
from cv_analyzer import analyze_cv, _clean_filename_hint
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
    ignored_emails = cfg.get("ignored_emails", [])
    emails = scrape_gmail(search_cfg["gmail_since"], couple_keywords, processed_ids, ignored_emails)
    print(f"[scraper] Nuevos mails con CV: {len(emails)}")

    imported = 0
    imported_uids: list[str] = []  # UIDs de mails importados → marcar como leídos al final

    for mail_data in emails:
        message_id  = mail_data["message_id"]
        sender_name = mail_data["sender_name"]
        sender_email = mail_data["sender_email"]
        body        = mail_data["body"]
        is_couple   = mail_data["is_couple"]
        attachments = mail_data["attachments"]

        if not attachments:
            # Sin adjunto — analizar solo con bio
            result = analyze_cv(
                cv_text="", bio=body,
                filename_hint=sender_name,
                positions=positions,
            )
            create_candidate({
                "search_id": search_id,
                "name": result["full_name"],
                "email": sender_email,
                "bio": body,
                "pdf_url": "",
                "pdf_text": "",
                "gmail_message_id": message_id,
                "position": result["position"] if result["position"] != "unknown" else "Host",
                "category": "solo",
                "status": "nuevo",
                "ai_score": result["score"],
                "ai_summary": result["summary"],
                "ai_strengths": result["strengths"],
                "ai_gaps": result["gaps"],
                "age": result["age"],
                "nationality": result["nationality"],
            })
            imported += 1
            imported_uids.append(message_id)
            print(f"[scraper] Candidato importado (sin CV): {result['full_name']} — score: {result['score']}")
            continue

        # Extraer texto de cada adjunto (máx 2)
        atts = attachments[:2]
        att_data = []
        for att in atts:
            cv_text = extract_attachment_text(att["filename"], att["bytes"])
            att_data.append({"att": att, "cv_text": cv_text})

        # Mismo nombre en los dos adjuntos → CV + carta de recomendación, no pareja
        # Hacemos una detección rápida comparando el filename hint
        if len(att_data) == 2:
            hint0 = _clean_filename_hint(att_data[0]["att"]["filename"])
            hint1 = _clean_filename_hint(att_data[1]["att"]["filename"])
            if hint0.lower() == hint1.lower():
                best = att_data[0] if len(att_data[0]["cv_text"]) >= len(att_data[1]["cv_text"]) else att_data[1]
                att_data = [best]
                is_couple = False
                print(f"[scraper] 2 adjuntos con mismo nombre — tomando el de mayor contenido")

        actually_couple = len(att_data) == 2

        # Analizar cada CV en una sola llamada
        results = []
        for i, ad in enumerate(att_data):
            partner_idx = 1 - i
            partner_name = ""
            partner_cv   = ""
            if actually_couple and partner_idx < len(att_data):
                # Para el partner, usamos el filename como pista de nombre
                partner_cv = att_data[partner_idx]["cv_text"]
                partner_name = _clean_filename_hint(att_data[partner_idx]["att"]["filename"])

            result = analyze_cv(
                cv_text=ad["cv_text"],
                bio=body if i == 0 else "",
                filename_hint=att_data[i]["att"]["filename"],
                positions=positions,
                is_couple=actually_couple,
                partner_name=partner_name,
                partner_cv_text=partner_cv,
            )
            results.append(result)

        # Mismo nombre detectado por Claude en los dos adjuntos → no es pareja
        if actually_couple and results[0]["full_name"].strip().lower() == results[1]["full_name"].strip().lower():
            print(f"[scraper] Mismo nombre ('{results[0]['full_name']}') — no es pareja, tomando el de mayor contenido")
            best_idx = 0 if len(att_data[0]["cv_text"]) >= len(att_data[1]["cv_text"]) else 1
            att_data  = [att_data[best_idx]]
            results   = [results[best_idx]]
            actually_couple = False

        candidate_ids = []
        for i, (ad, result) in enumerate(zip(att_data, results)):
            pdf_url = upload_pdf(search_id, f"{message_id}_{i}_{ad['att']['filename']}", ad["att"]["bytes"])
            pos_title = result["position"] if result["position"] != "unknown" else "Host"

            candidate_id = create_candidate({
                "search_id": search_id,
                "name": result["full_name"],
                "email": sender_email if i == 0 else "",
                "bio": body if i == 0 else "",
                "pdf_url": pdf_url,
                "pdf_text": ad["cv_text"],
                "gmail_message_id": message_id if i == 0 else f"{message_id}_p2",
                "position": pos_title,
                "category": "couple" if actually_couple else "solo",
                "status": "nuevo",
                "ai_score": result["score"],
                "ai_summary": result["summary"],
                "ai_strengths": result["strengths"],
                "ai_gaps": result["gaps"],
                "age": result["age"],
                "nationality": result["nationality"],
            })
            candidate_ids.append(candidate_id)
            imported += 1
            print(f"[scraper] Candidato importado: {result['full_name']} — score: {result['score']}")

        if len(candidate_ids) == 2:
            link_couple(candidate_ids[0], candidate_ids[1])
            print(f"[scraper] Pareja vinculada: {candidate_ids[0]} <-> {candidate_ids[1]}")

        if candidate_ids:
            imported_uids.append(message_id)

    # Marcar como leídos solo los mails de los que importamos candidatos
    mark_messages_seen(imported_uids)

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
