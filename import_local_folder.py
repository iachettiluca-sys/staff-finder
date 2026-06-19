#!/usr/bin/env python3
"""
import_local_folder.py — Importa CVs desde una carpeta local (descargados de Drive).
Corre UNA SOLA VEZ para cargar el batch inicial.

Uso:
    python import_local_folder.py "C:/Users/lucai/Downloads/CVs"

Acepta PDFs y DOCX. Para parejas: si dos archivos tienen nombres similares o
están en la misma subcarpeta, los vincula. Si tiene dudas, los importa solos
y mandá un mail de consulta.
"""
from __future__ import annotations
import sys, os, re, smtplib, ssl
from pathlib import Path
from email.message import EmailMessage

root = Path(__file__).resolve().parent
sys.path.insert(0, str(root / "src"))

from dotenv import load_dotenv
load_dotenv(root / ".env")

import yaml
from pdf_extractor import extract_attachment_text
from name_extractor import extract_name_and_position
from cv_matcher import match_cv
from supabase_ops import (
    get_or_create_search, get_processed_message_ids,
    ensure_positions, upload_pdf, create_candidate, link_couple,
)

SUPPORTED_EXTS = {".pdf", ".doc", ".docx"}


def send_email(subject: str, body: str) -> None:
    host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    port = int(os.environ.get("SMTP_PORT", "587"))
    user = os.environ.get("SMTP_USER")
    pwd  = os.environ.get("SMTP_PASS")
    to   = os.environ.get("NOTIFY_TO", user)
    if not (user and pwd and to):
        print(f"[notify] {subject}\n{body}")
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


def _get_cv_files(folder: Path) -> list[Path]:
    """Retorna todos los archivos de CV en la carpeta (recursivo)."""
    files = []
    for f in sorted(folder.rglob("*")):
        if f.suffix.lower() in SUPPORTED_EXTS and not f.name.startswith("."):
            files.append(f)
    return files


def _detect_couple_files(files: list[Path]) -> list[tuple]:
    """
    Agrupa archivos que parecen ser de una pareja.
    Heurística: están en la misma subcarpeta O sus nombres son casi idénticos
    (misma base con sufijo 1/2 o _A/_B).
    Retorna lista de (file1, file2_or_None).
    """
    grouped = []
    used = set()

    # Agrupar por subcarpeta primero
    by_folder: dict[Path, list[Path]] = {}
    for f in files:
        by_folder.setdefault(f.parent, []).append(f)

    for folder_files in by_folder.values():
        if len(folder_files) == 2:
            grouped.append((folder_files[0], folder_files[1]))
            used.update(folder_files)
        elif len(folder_files) == 1:
            if folder_files[0] not in used:
                grouped.append((folder_files[0], None))
                used.add(folder_files[0])
        else:
            # Más de 2 en la misma carpeta — agregar individualmente
            for f in folder_files:
                if f not in used:
                    grouped.append((f, None))
                    used.add(f)

    # Archivos sin agrupar
    for f in files:
        if f not in used:
            grouped.append((f, None))

    return grouped


def process_file(f: Path, positions: list[dict], search_id: str,
                 processed_ids: set, bio: str = "") -> dict | None:
    """Procesa un archivo CV: extrae texto, nombre, puesto y matchea con Claude."""
    fake_id = f"local_{f.stem}"
    if fake_id in processed_ids:
        print(f"  [skip] Ya importado: {f.name}")
        return None

    file_bytes = f.read_bytes()
    cv_text = extract_attachment_text(f.name, file_bytes)

    info = extract_name_and_position(cv_text, bio, f.stem)
    name = info["full_name"]
    position = info["position"]

    # Buscar requisitos del puesto
    pos_cfg = next((p for p in positions if p["title"] == position), positions[0] if positions else None)
    pos_title = pos_cfg["title"] if pos_cfg else position
    pos_req = pos_cfg["requirements"] if pos_cfg else ""

    pdf_url = upload_pdf(search_id, f"local_{f.name}", file_bytes)

    match = match_cv(
        cv_text=cv_text, bio=bio,
        candidate_name=name,
        position_title=pos_title,
        position_requirements=pos_req,
    )

    print(f"  {name} → {pos_title} — score: {match['score']}")
    return {
        "file_bytes": file_bytes,
        "filename": f.name,
        "fake_id": fake_id,
        "name": name,
        "position": pos_title,
        "cv_text": cv_text,
        "pdf_url": pdf_url,
        "match": match,
    }


def main() -> int:
    if len(sys.argv) < 2:
        print("Uso: python import_local_folder.py <carpeta>")
        return 1

    folder = Path(sys.argv[1])
    if not folder.exists():
        print(f"Carpeta no encontrada: {folder}")
        return 1

    cfg = yaml.safe_load((root / "config.yaml").read_text(encoding="utf-8"))
    search_cfg = cfg["search"]
    positions_cfg = cfg["positions"]

    search_id = get_or_create_search(search_cfg["name"], search_cfg["company"])
    positions = ensure_positions(search_id, positions_cfg)
    processed_ids = get_processed_message_ids(search_id)

    files = _get_cv_files(folder)
    print(f"\nEncontrados {len(files)} archivos en {folder}")

    groups = _detect_couple_files(files)
    print(f"Grupos detectados: {len(groups)} ({sum(1 for g in groups if g[1])} parejas)\n")

    imported = 0
    couple_questions = []

    for f1, f2 in groups:
        if f2:
            # Pareja
            print(f"Pareja: {f1.name} + {f2.name}")
            d1 = process_file(f1, positions, search_id, processed_ids)
            d2 = process_file(f2, positions, search_id, processed_ids)

            if d1 and d2:
                # Mismo nombre → no es pareja, es CV + carta de recomendación u otro doc
                if d1["name"].strip().lower() == d2["name"].strip().lower():
                    print(f"  [mismo nombre] '{d1['name']}' — no es pareja, importando el de mayor contenido")
                    best = d1 if len(d1["cv_text"]) >= len(d2["cv_text"]) else d2
                    create_candidate({
                        "search_id": search_id, "name": best["name"], "email": "",
                        "bio": "", "pdf_url": best["pdf_url"], "pdf_text": best["cv_text"],
                        "gmail_message_id": best["fake_id"], "position": best["position"],
                        "category": "solo", "status": "nuevo",
                        "ai_score": best["match"]["score"], "ai_summary": best["match"]["summary"],
                        "ai_strengths": best["match"]["strengths"], "ai_gaps": best["match"]["gaps"],
                    })
                    imported += 1
                else:
                    # Nombres distintos → pareja real
                    pos_cfg = next((p for p in positions if p["title"] == d1["position"]), positions[0])
                    match_pair = match_cv(
                        cv_text=d1["cv_text"], bio="",
                        candidate_name=d1["name"],
                        position_title=d1["position"],
                        position_requirements=pos_cfg["requirements"],
                        is_couple=True,
                        partner_name=d2["name"],
                        partner_cv_text=d2["cv_text"],
                    )
                    id1 = create_candidate({
                        "search_id": search_id, "name": d1["name"], "email": "",
                        "bio": "", "pdf_url": d1["pdf_url"], "pdf_text": d1["cv_text"],
                        "gmail_message_id": d1["fake_id"], "position": d1["position"],
                        "category": "couple", "status": "nuevo",
                        "ai_score": match_pair["score"], "ai_summary": match_pair["summary"],
                        "ai_strengths": match_pair["strengths"], "ai_gaps": match_pair["gaps"],
                    })
                    id2 = create_candidate({
                        "search_id": search_id, "name": d2["name"], "email": "",
                        "bio": "", "pdf_url": d2["pdf_url"], "pdf_text": d2["cv_text"],
                        "gmail_message_id": d2["fake_id"], "position": d2["position"],
                        "category": "couple", "status": "nuevo",
                        "ai_score": match_pair["score"], "ai_summary": match_pair["summary"],
                        "ai_strengths": match_pair["strengths"], "ai_gaps": match_pair["gaps"],
                    })
                    link_couple(id1, id2)
                    imported += 2
        else:
            # Individual
            print(f"Individual: {f1.name}")
            d = process_file(f1, positions, search_id, processed_ids)
            if d:
                create_candidate({
                    "search_id": search_id, "name": d["name"], "email": "",
                    "bio": "", "pdf_url": d["pdf_url"], "pdf_text": d["cv_text"],
                    "gmail_message_id": d["fake_id"], "position": d["position"],
                    "category": "solo", "status": "nuevo",
                    "ai_score": d["match"]["score"], "ai_summary": d["match"]["summary"],
                    "ai_strengths": d["match"]["strengths"], "ai_gaps": d["match"]["gaps"],
                })
                imported += 1

    print(f"\nListo. Importados: {imported} candidatos")
    if imported > 0:
        send_email(
            f"Staff Finder — {imported} CVs importados desde carpeta local",
            f"Se importaron {imported} candidatos desde la carpeta:\n{folder}\n\nRevisalos en la app.",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
