#!/usr/bin/env python3
"""
Limpia candidatos basura después de una corrida del scraper.

IMPORTANTE: En lugar de BORRAR registros basura, los marca status='spam'.
Esto mantiene el gmail_message_id en la tabla, así el scraper no los
reimporta en la próxima corrida. El frontend ignora status='spam'.
"""
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
    return re.sub(r"\s+", " ", n.encode("ascii", "ignore").decode())

# Nombres que siempre son spam (sin importar de qué mail vengan)
SPAM_NAMES = {
    "zulki",
    "lucas",
    "mang mamex",
    "gabriel smith",       # sin CV, score mínimo, nombre genérico
    "guillermo anziani",   # sin CV, score 25
    "santiago guinazu llorente",  # MBA industrial, score 5, no es candidato
}

# Palabras en el nombre que indican empresa, no persona
SPAM_COMPANY_HINTS = [
    "werben hr", "consulting", "catering", "food consulting",
    "chefs effect", "the chefs", "vza food",
]

res = sb.table("candidates").select(
    "id,name,gmail_message_id,ai_score,status"
).execute()
rows = [r for r in (res.data or []) if r.get("status") != "spam"]

to_spam = []
for r in rows:
    n = norm(r["name"])
    if n in SPAM_NAMES:
        to_spam.append(r)
        continue
    if any(hint in n for hint in SPAM_COMPANY_HINTS):
        to_spam.append(r)

print(f"Marcando como spam {len(to_spam)} registros:")
for r in to_spam:
    print(f"  {r['name']} | score={r['ai_score']}")

if to_spam:
    ids = [r["id"] for r in to_spam]
    sb.table("candidates").update({"couple_partner_id": None}).in_("couple_partner_id", ids).execute()
    sb.table("candidates").update({"couple_partner_id": None}).in_("id", ids).execute()
    sb.table("candidates").update({"status": "spam"}).in_("id", ids).execute()

# Dedup por nombre (solo candidatos no-spam)
all_rows = sb.table("candidates").select(
    "id,name,position,couple_partner_id,pdf_text,bio,ai_score,status"
).neq("status", "spam").execute().data or []

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

dup_spam = []
print("\nDuplicados por nombre:")
for key, group in sorted(groups.items()):
    if len(group) < 2:
        continue
    keeper = best(group)
    dupes = [r for r in group if r["id"] != keeper["id"]]
    dup_spam.extend(d["id"] for d in dupes)
    print(f"  [{len(group)}x] {group[0]['name']} → KEEP score={keeper['ai_score']}")
    for d in dupes:
        print(f"    SPAM score={d['ai_score']} | cv={len(d.get('pdf_text') or '')}c")

if dup_spam:
    sb.table("candidates").update({"couple_partner_id": None}).in_("couple_partner_id", dup_spam).execute()
    sb.table("candidates").update({"couple_partner_id": None}).in_("id", dup_spam).execute()
    sb.table("candidates").update({"status": "spam"}).in_("id", dup_spam).execute()
    print(f"\nMarcados como spam: {len(dup_spam)} duplicados.")

final = sb.table("candidates").select(
    "id,name,position,ai_score"
).neq("status", "spam").execute().data or []

chef = [r for r in final if r["position"] == "Chef"]
host = [r for r in final if r["position"] == "Host"]
print(f"\nCandidatos activos: {len(final)} | Chef: {len(chef)} | Host: {len(host)}")
