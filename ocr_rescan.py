#!/usr/bin/env python3
"""
Re-procesa con OCR todos los candidatos que tienen PDF pero sin texto extraído.
Actualiza pdf_text, posición y score en la DB.
"""
import sys, json, urllib.request
from pathlib import Path
root = Path(__file__).resolve().parent
sys.path.insert(0, str(root / "src"))
from dotenv import load_dotenv; load_dotenv(root / ".env")

import yaml
from supabase_ops import get_client
from pdf_extractor import extract_text_from_pdf
from cv_matcher import match_cv
from fix_positions import detect_position

sb = get_client()
cfg = yaml.safe_load((root / "config.yaml").read_text(encoding="utf-8"))
positions_cfg = cfg["positions"]
pos_map = {p["title"]: p for p in positions_cfg}

res = sb.table("candidates").select(
    "id,name,position,pdf_url,pdf_text,bio,ai_score"
).execute()

# Candidatos con PDF pero sin texto (o muy poco)
targets = [
    r for r in (res.data or [])
    if r.get("pdf_url") and len((r.get("pdf_text") or "").strip()) < 50
]

print(f"Candidatos a re-procesar con OCR: {len(targets)}\n")

for c in targets:
    print(f"  {c['name']} | score actual={c['ai_score']}")
    try:
        with urllib.request.urlopen(c["pdf_url"]) as resp:
            pdf_bytes = resp.read()
    except Exception as e:
        print(f"    ERROR descargando PDF: {e}")
        continue

    text = extract_text_from_pdf(pdf_bytes)
    if len(text) < 30:
        print(f"    OCR insuficiente ({len(text)} chars) — sigue sin texto legible")
        continue

    print(f"    OCR: {len(text)} chars extraidos")

    # Re-detectar posición con el nuevo texto
    new_pos, reason = detect_position(text, c.get("bio") or "", c.get("position") or "Chef")
    print(f"    Posición: {c.get('position')} -> {new_pos}  ({reason})")

    pos_cfg = pos_map.get(new_pos, positions_cfg[0])
    match = match_cv(
        cv_text=text,
        bio=c.get("bio") or "",
        candidate_name=c["name"],
        position_title=pos_cfg["title"],
        position_requirements=pos_cfg["requirements"],
    )
    print(f"    Score: {c['ai_score']} -> {match['score']}")

    sb.table("candidates").update({
        "pdf_text": text,
        "position": new_pos,
        "ai_score": match["score"],
        "ai_summary": match["summary"],
        "ai_strengths": json.dumps(match["strengths"], ensure_ascii=False),
        "ai_gaps": json.dumps(match["gaps"], ensure_ascii=False),
    }).eq("id", c["id"]).execute()
    print(f"    Actualizado.\n")

print("Listo.")
