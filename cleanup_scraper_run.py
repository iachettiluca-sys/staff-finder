#!/usr/bin/env python3
"""Limpia los candidatos basura que entró en la última corrida del scraper."""
import sys
from pathlib import Path
root = Path(__file__).resolve().parent
sys.path.insert(0, str(root / "src"))
from dotenv import load_dotenv; load_dotenv(root / ".env")
from supabase_ops import get_client
import re, unicodedata

sb = get_client()

def norm(s):
    n = unicodedata.normalize("NFD", (s or "").lower().strip())
    return re.sub(r"\s+", " ", n.encode("ascii","ignore").decode())

# Candidatos a borrar directamente
NAMES_TO_DELETE = [
    "zulki",       # sin CV, score 5
    "lucas",       # auto-emails del propio usuario
]
# Por email sender
EMAILS_TO_DELETE = [
    "lucas@theriverhousegroup.com",
]

res = sb.table("candidates").select("id,name,gmail_message_id,ai_score").execute()
rows = res.data or []

to_delete = []
for r in rows:
    n = norm(r["name"])
    if n in NAMES_TO_DELETE:
        to_delete.append(r)
        continue
    # también por email si lo tenemos en el nombre (fallback)
    if any(em in (r.get("gmail_message_id") or "").lower() for em in EMAILS_TO_DELETE):
        to_delete.append(r)

print(f"Borrando {len(to_delete)} registros basura:")
for r in to_delete:
    print(f"  {r['name']} | score={r['ai_score']}")

if to_delete:
    ids = [r["id"] for r in to_delete]
    sb.table("candidates").update({"couple_partner_id": None}).in_("couple_partner_id", ids).execute()
    sb.table("candidates").update({"couple_partner_id": None}).in_("id", ids).execute()
    sb.table("candidates").delete().in_("id", ids).execute()

# Ahora dedup por nombre (igual que antes)
all_rows = sb.table("candidates").select(
    "id,name,position,couple_partner_id,pdf_text,bio,ai_score"
).execute().data or []

groups = {}
for r in all_rows:
    key = norm(r["name"])
    groups.setdefault(key, []).append(r)

def best(group):
    return max(group, key=lambda c: (
        1 if c.get("couple_partner_id") else 0,
        1 if (c.get("bio") or "").strip() else 0,
        len(c.get("pdf_text") or ""),
        c.get("ai_score") or 0,
    ))

dup_delete = []
print("\nDuplicados por nombre:")
for key, group in sorted(groups.items()):
    if len(group) < 2:
        continue
    keeper = best(group)
    dupes = [r for r in group if r["id"] != keeper["id"]]
    dup_delete.extend(d["id"] for d in dupes)
    print(f"  [{len(group)}x] {group[0]['name']} → KEEP score={keeper['ai_score']}")
    for d in dupes:
        print(f"    DEL score={d['ai_score']} | cv={len(d.get('pdf_text') or '')}c")

if dup_delete:
    sb.table("candidates").update({"couple_partner_id": None}).in_("couple_partner_id", dup_delete).execute()
    sb.table("candidates").update({"couple_partner_id": None}).in_("id", dup_delete).execute()
    for i in range(0, len(dup_delete), 50):
        sb.table("candidates").delete().in_("id", dup_delete[i:i+50]).execute()
    print(f"\nEliminados {len(dup_delete)} duplicados.")

final = sb.table("candidates").select("id,name,position,ai_score").execute().data or []
chef = [r for r in final if r["position"] == "Chef"]
host = [r for r in final if r["position"] == "Host"]
print(f"\nTotal: {len(final)} | Chef: {len(chef)} | Host: {len(host)}")
print("\nNuevos candidatos (score puede ser 0 si hay que re-evaluar):")
for r in sorted(final, key=lambda x: x["name"]):
    print(f"  [{r['position']}] {r['name']} | {r['ai_score']}")
