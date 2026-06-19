#!/usr/bin/env python3
"""
fix_aftermath.py — Limpieza post fix_positions:
1. Restaura Michaela van Wyk (borrada por dedup incorrecto)
2. Corrige nombres de alias/email
3. Corrige dedup bad picks (el nombre real es mejor que el alias)
"""
from __future__ import annotations
import sys, json
from pathlib import Path

root = Path(__file__).resolve().parent
sys.path.insert(0, str(root / "src"))
from dotenv import load_dotenv; load_dotenv(root / ".env")
from supabase_ops import get_client
sb = get_client()

def find(name_fragment: str):
    res = sb.table("candidates").select("id,name,position,couple_partner_id,pdf_text,bio,category").execute()
    frag = name_fragment.lower()
    return [r for r in (res.data or []) if frag in r["name"].lower()]

def get_all():
    return sb.table("candidates").select("id,name,position,couple_partner_id,pdf_text,bio,category,ai_score").execute().data or []

all_rows = get_all()
by_id = {r["id"]: r for r in all_rows}

# ── 1. Corregir nombres alias → nombre real ──────────────────────────────────
print("=== CORRECCIÓN DE NOMBRES ===")

RENAMES = {
    "joteiza61@gmail.com":    "Javier Oteiza Aguirre",
    "cucina vagante":          "Carlos Luis Velarde Escalona",
    "El Caldero":              "Juan Andrés Bustamante Vergara",
    "Juan Ignacio":            "Juan Ignacio Perez Daldi",
    "Juancho Pedrini":         "Juan Manuel Pedrini",  # Juancho es apodo de Juan Manuel
    "dana van wyk":            "Dana van Wyk",          # capitalización
    "luca tomassetti":         "Luca Tomassetti",
    "martina quinodoz":        "Martina Quinodoz",
    "viviana godnic":          "Viviana Godnic",
    "andrew watts":            "Andrew Watts",
    "Angeles vr":              "Angeles Villanueva Ruiz",  # completar nombre si es posible
}

for old, new in RENAMES.items():
    matches = [r for r in all_rows if r["name"].lower().strip() == old.lower().strip()]
    for m in matches:
        if m["name"] != new:
            print(f"  Renombrando: '{m['name']}' -> '{new}'")
            sb.table("candidates").update({"name": new}).eq("id", m["id"]).execute()

# ── 2. Restaurar Michaela van Wyk ────────────────────────────────────────────
print("\n=== RESTAURANDO MICHAELA VAN WYK ===")

dana = next((r for r in all_rows if "dana" in r["name"].lower() and "wyk" in r["name"].lower()), None)
michaela_existing = next((r for r in all_rows if "michaela" in r["name"].lower()), None)

if michaela_existing:
    print("  Michaela ya existe, no hace falta restaurar.")
elif dana:
    # El cv_text de dana ES el de Michaela (la pareja compartió archivo)
    # Creamos Michaela como candidata separada con el mismo CV
    michaela_record = {
        "name": "Michaela van Wyk",
        "position": "Host",
        "category": "couple",
        "couple_partner_id": dana["id"],
        "pdf_text": dana.get("pdf_text") or "",
        "bio": dana.get("bio") or "",
        "ai_score": 0,
        "ai_summary": "",
        "ai_strengths": "[]",
        "ai_gaps": "[]",
        "search_id": None,  # se setea abajo
    }
    # Obtener search_id de dana
    dana_full = sb.table("candidates").select("search_id").eq("id", dana["id"]).execute()
    search_id = (dana_full.data or [{}])[0].get("search_id")
    if search_id:
        michaela_record["search_id"] = search_id

    res = sb.table("candidates").insert(michaela_record).execute()
    new_m = (res.data or [{}])[0]
    new_mid = new_m.get("id")
    if new_mid:
        # Linkar dana -> michaela
        sb.table("candidates").update({
            "couple_partner_id": new_mid,
            "category": "couple",
        }).eq("id", dana["id"]).execute()
        print(f"  Michaela van Wyk creada (id={new_mid[:8]}) y linkeada con Dana.")
    else:
        print(f"  ERROR: No se pudo crear Michaela. Respuesta: {res}")
else:
    print("  Dana tampoco encontrada — revisar manualmente.")

# ── 3. Eliminar registros que claramente no son candidatos ───────────────────
print("\n=== REGISTROS A REVISAR ===")
# Angeles VR — nombre incompleto, verificar
angeles = [r for r in all_rows if "angeles" in r["name"].lower()]
for a in angeles:
    print(f"  Angeles encontrada: '{a['name']}' | score={a['ai_score']} | pos={a['position']}")

# ── 4. Resumen final ─────────────────────────────────────────────────────────
final = get_all()
chef_n = sum(1 for r in final if r["position"] == "Chef")
host_n = sum(1 for r in final if r["position"] == "Host")
couple_n = sum(1 for r in final if r["category"] == "couple")
print(f"\n=== ESTADO FINAL ===")
print(f"  Total: {len(final)}  |  Chef: {chef_n}  |  Host: {host_n}  |  En pareja: {couple_n}")
