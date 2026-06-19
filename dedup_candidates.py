#!/usr/bin/env python3
"""
dedup_candidates.py — Elimina candidatos duplicados.
Criterio de selección: bio > más cv_text > mayor score.
Maneja tanto duplicados exactos (mismo nombre, diferente case) como
near-duplicates conocidos (Flor/Florencia, nombre parcial, etc.).
"""
from __future__ import annotations
import sys, re
from pathlib import Path

root = Path(__file__).resolve().parent
sys.path.insert(0, str(root / "src"))
from dotenv import load_dotenv
load_dotenv(root / ".env")

from supabase_ops import get_client

sb = get_client()


def normalize(name: str) -> str:
    """Lowercase, strip, collapse spaces, remove accents for comparison."""
    import unicodedata
    n = unicodedata.normalize("NFD", name.lower().strip())
    n = n.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s+", " ", n)


def best(candidates: list[dict]) -> dict:
    """Pick the best record to keep: prefer bio, then longer cv_text, then higher score.
    Also prefer the one linked as a couple."""
    def key(c):
        is_couple = 1 if c.get("couple_partner_id") else 0
        has_bio = 1 if (c.get("bio") or "").strip() else 0
        cv_len = len(c.get("pdf_text") or "")
        score = c.get("ai_score") or 0
        return (is_couple, has_bio, cv_len, score)
    return max(candidates, key=key)


def get_all():
    res = sb.table("candidates").select(
        "id,name,category,couple_partner_id,pdf_text,bio,ai_score,gmail_message_id"
    ).execute()
    return res.data or []


def main():
    candidates = get_all()

    # --- Known near-duplicate aliases ---
    # Map normalized alias → normalized canonical
    aliases = {
        "flor vrljicak":           "florencia vrljicak",
        "francisca kenny":         "francisca ines kenny",
        "gustavo maldonado":       "gustavo h. maldonado",
        "gustavo horario maldonado": "gustavo h. maldonado",
        "juan ignacio":            "juan ignacio perez daldi",
        "matias roman pereyra":    "matias roman pereyra",  # same, just accent
        "dana van wyk":            "dana van wyk",          # case only
    }

    # Build groups by normalized name (applying aliases)
    groups: dict[str, list[dict]] = {}
    for c in candidates:
        key = normalize(c["name"])
        key = aliases.get(key, key)
        groups.setdefault(key, []).append(c)

    to_delete: list[str] = []
    to_keep_ids: set[str] = set()

    print("=== Duplicados encontrados ===\n")
    dup_count = 0
    for norm_name, group in sorted(groups.items()):
        if len(group) == 1:
            to_keep_ids.add(group[0]["id"])
            continue

        keeper = best(group)
        dupes  = [c for c in group if c["id"] != keeper["id"]]
        to_keep_ids.add(keeper["id"])
        to_delete.extend(d["id"] for d in dupes)
        dup_count += len(dupes)

        print(f"[{len(group)}x] {group[0]['name']}")
        print(f"  KEEP  : score={keeper['ai_score']} | bio={'si' if keeper.get('bio') else 'no'} | "
              f"cv={len(keeper.get('pdf_text') or '')}c | id={keeper['id'][:8]}")
        for d in dupes:
            print(f"  DELETE: score={d['ai_score']} | bio={'si' if d.get('bio') else 'no'} | "
                  f"cv={len(d.get('pdf_text') or '')}c | id={d['id'][:8]}")
        print()

    print(f"Total a eliminar: {dup_count} registros")

    if not to_delete:
        print("No hay duplicados.")
        return

    print("\nEliminando...")

    # 1. Clear FK references pointing TO records we'll delete
    sb.table("candidates").update({"couple_partner_id": None}).in_(
        "couple_partner_id", to_delete
    ).execute()

    # 2. Clear FK references FROM records we'll delete
    sb.table("candidates").update({"couple_partner_id": None}).in_(
        "id", to_delete
    ).execute()

    # 3. Delete in batches of 50
    for i in range(0, len(to_delete), 50):
        batch = to_delete[i:i+50]
        sb.table("candidates").delete().in_("id", batch).execute()

    print(f"Eliminados: {len(to_delete)}")

    # Final count
    final = get_all()
    print(f"\nCandidatos restantes: {len(final)}")
    couples = [c for c in final if c["category"] == "couple"]
    if couples:
        by_id = {c["id"]: c for c in final}
        shown = set()
        print("Parejas activas:")
        for c in couples:
            pid = c.get("couple_partner_id")
            pair = frozenset([c["id"], pid or ""])
            if pair in shown: continue
            shown.add(pair)
            partner = by_id.get(pid)
            print(f"  {c['name']} + {partner['name'] if partner else '?'}")


if __name__ == "__main__":
    main()
