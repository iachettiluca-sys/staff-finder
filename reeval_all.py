#!/usr/bin/env python3
"""
reeval_all.py — Re-evalúa todos los candidatos con el cv_matcher actualizado.
Usa el pdf_text y bio ya guardados en la BD. No necesita acceso a Storage.
"""
from __future__ import annotations
import sys, time
from pathlib import Path

root = Path(__file__).resolve().parent
sys.path.insert(0, str(root / "src"))
from dotenv import load_dotenv
load_dotenv(root / ".env")

import yaml
from cv_matcher import match_cv
from supabase_ops import get_client, get_or_create_search, ensure_positions

sb = get_client()
cfg = yaml.safe_load((root / "config.yaml").read_text(encoding="utf-8"))
search_cfg = cfg["search"]
positions_cfg = cfg["positions"]

search_id = get_or_create_search(search_cfg["name"], search_cfg["company"])
positions = ensure_positions(search_id, positions_cfg)
pos_map = {p["title"]: p for p in positions}

res = sb.table("candidates").select(
    "id,name,position,category,couple_partner_id,pdf_text,bio,ai_score"
).eq("search_id", search_id).execute()

candidates = res.data or []
by_id = {c["id"]: c for c in candidates}

print(f"Re-evaluando {len(candidates)} candidatos...\n")

updated = 0
for i, c in enumerate(candidates, 1):
    name = c["name"]
    pos_title = c["position"] or "unknown"
    pos_cfg = pos_map.get(pos_title) or pos_map.get("Chef") or positions[0]
    cv_text = c.get("pdf_text") or ""
    bio = c.get("bio") or ""

    is_couple = c["category"] == "couple"
    partner_name = ""
    partner_cv = ""
    if is_couple and c.get("couple_partner_id"):
        partner = by_id.get(c["couple_partner_id"])
        if partner:
            partner_name = partner["name"]
            partner_cv = partner.get("pdf_text") or ""

    match = match_cv(
        cv_text=cv_text,
        bio=bio,
        candidate_name=name,
        position_title=pos_cfg["title"],
        position_requirements=pos_cfg["requirements"],
        is_couple=is_couple,
        partner_name=partner_name,
        partner_cv_text=partner_cv,
    )

    import json
    sb.table("candidates").update({
        "ai_score": match["score"],
        "ai_summary": match["summary"],
        "ai_strengths": json.dumps(match["strengths"], ensure_ascii=False),
        "ai_gaps": json.dumps(match["gaps"], ensure_ascii=False),
    }).eq("id", c["id"]).execute()

    print(f"[{i}/{len(candidates)}] {name} ({pos_title}) -> score: {match['score']}")
    updated += 1

    # Small pause to avoid rate limiting
    if i % 10 == 0:
        time.sleep(1)

print(f"\nListo. {updated} candidatos re-evaluados.")
