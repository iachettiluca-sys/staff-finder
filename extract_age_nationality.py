#!/usr/bin/env python3
"""
extract_age_nationality.py — Extrae edad y nacionalidad para todos los candidatos existentes.
Corre una sola vez (o después de importar candidatos sin estos datos).
"""
import sys, time
from pathlib import Path
root = Path(__file__).resolve().parent
sys.path.insert(0, str(root / "src"))
from dotenv import load_dotenv; load_dotenv(root / ".env")

from supabase_ops import get_client
from age_nationality_extractor import extract_age_nationality

sb = get_client()

res = sb.table("candidates").select(
    "id,name,pdf_text,bio,age,nationality"
).neq("status", "spam").execute()

# Procesar candidatos sin edad Y sin nacionalidad
candidates = [
    c for c in (res.data or [])
    if c.get("age") is None and c.get("nationality") is None
]

print(f"Candidatos a procesar: {len(candidates)}\n")

for i, c in enumerate(candidates, 1):
    print(f"[{i}/{len(candidates)}] {c['name']}...", end=" ", flush=True)
    profile = extract_age_nationality(
        c.get("pdf_text") or "",
        c.get("bio") or "",
    )
    age = profile["age"]
    nat = profile["nationality"]
    print(f"edad={age}  nac={nat}")
    sb.table("candidates").update({
        "age": age,
        "nationality": nat,
    }).eq("id", c["id"]).execute()
    time.sleep(0.25)

print(f"\nListo. {len(candidates)} candidatos procesados.")
