#!/usr/bin/env python3
"""
Deletes candidates with no name or score=0 so import_local_folder re-imports them
with the fixed name_extractor and cv_matcher.
"""
from __future__ import annotations
import sys, os
from pathlib import Path

root = Path(__file__).resolve().parent
sys.path.insert(0, str(root / "src"))
from dotenv import load_dotenv
load_dotenv(root / ".env")

from supabase_ops import get_client

sb = get_client()
res = sb.table("candidates").select("id,name,ai_score,ai_summary,gmail_message_id").execute()

to_delete = []
for c in res.data or []:
    name = (c.get("name") or "").strip()
    score = c.get("ai_score") or 0
    summary = c.get("ai_summary") or ""
    if name.lower() in ("desconocido", "unknown", "") or (score == 0 and "escaneado" not in summary):
        to_delete.append(c)

print(f"Candidatos a re-procesar: {len(to_delete)}")
for c in to_delete:
    print(f"  [{c['gmail_message_id']}] {c['name']} -- score {c['ai_score']}")

if not to_delete:
    sys.exit(0)

ids = [c["id"] for c in to_delete]
sb.table("candidates").delete().in_("id", ids).execute()
print(f"\nEliminados {len(ids)} registros. Correr de nuevo: python import_local_folder.py <zip>")
