#!/usr/bin/env python3
"""
fix_false_couples.py
Cleans up incorrectly linked couples in the DB.

Rules:
- A couple is valid ONLY if both people have genuinely different names.
- Records named "{name} (2)" where the partner has the same base name = false couple.
  → Delete the (2) record, reset the original to category='solo'.
- After cleanup, properly link Dana van Wyk ↔ Michaela van Wyk.
- Any remaining candidate marked category='couple' with no valid partner → reset to 'solo'.
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


def get_all_candidates():
    res = sb.table("candidates").select(
        "id,name,category,couple_partner_id,gmail_message_id,bio,ai_score"
    ).execute()
    return res.data or []


def base_name(name: str) -> str:
    """Strip trailing ' (2)', '(2)', etc. to get the base name."""
    return re.sub(r'\s*\(\d+\)\s*$', '', name).strip().lower()


def main():
    candidates = get_all_candidates()
    by_id = {c["id"]: c for c in candidates}

    deleted_ids = set()
    reset_ids = set()    # candidates to reset to solo
    linked_pairs = set() # already-processed pairs

    print("=== Analizando parejas ===\n")

    for c in candidates:
        if c["id"] in deleted_ids or c["category"] != "couple":
            continue

        partner_id = c.get("couple_partner_id")
        partner = by_id.get(partner_id) if partner_id else None

        c_base = base_name(c["name"])

        if not partner:
            # Linked couple but partner doesn't exist
            print(f"[sin pareja] {c['name']} → reset a solo")
            reset_ids.add(c["id"])
            continue

        p_base = base_name(partner["name"])

        pair_key = frozenset([c["id"], partner["id"]])
        if pair_key in linked_pairs:
            continue
        linked_pairs.add(pair_key)

        if c_base == p_base:
            # Same base name → false couple
            # Keep whichever has more info (bio or higher score), delete the other
            c_is_dup = c["name"].strip().lower() != c_base  # has (2) suffix
            p_is_dup = partner["name"].strip().lower() != p_base

            if c_is_dup:
                to_delete, to_keep = c, partner
            elif p_is_dup:
                to_delete, to_keep = partner, c
            else:
                # Neither has (2) — keep higher score
                to_delete = c if (c["ai_score"] or 0) <= (partner["ai_score"] or 0) else partner
                to_keep = partner if to_delete == c else c

            print(f"[falsa pareja] '{to_keep['name']}' + '{to_delete['name']}' → borro el duplicado, reseteo a solo")
            deleted_ids.add(to_delete["id"])
            reset_ids.add(to_keep["id"])
        else:
            print(f"[pareja válida] '{c['name']}' + '{partner['name']}' → OK")

    # --- Clear ALL couple_partner_id references first (FK constraint) ---
    all_affected = deleted_ids | reset_ids
    if all_affected:
        print(f"\nLimpiando referencias FK...")
        # Clear references pointing TO records we'll delete
        sb.table("candidates").update({"couple_partner_id": None}).in_(
            "couple_partner_id", list(all_affected)
        ).execute()
        # Clear references FROM records we'll delete/reset
        sb.table("candidates").update({"couple_partner_id": None}).in_(
            "id", list(all_affected)
        ).execute()

    # --- Execute deletions ---
    if deleted_ids:
        print(f"Eliminando {len(deleted_ids)} registros duplicados...")
        sb.table("candidates").delete().in_("id", list(deleted_ids)).execute()

    # --- Reset solo ---
    all_reset = reset_ids - deleted_ids
    if all_reset:
        print(f"Reseteando {len(all_reset)} candidatos a 'solo'...")
        sb.table("candidates").update({
            "category": "solo",
            "couple_partner_id": None
        }).in_("id", list(all_reset)).execute()

    # --- Properly link Dana van Wyk ↔ Michaela van Wyk ---
    print("\n=== Linkando Dana van Wyk + Michaela van Wyk ===")
    all_candidates = get_all_candidates()
    dana = next((c for c in all_candidates
                 if "dana" in c["name"].lower() and "wyk" in c["name"].lower()), None)
    michaela = next((c for c in all_candidates
                     if "michaela" in c["name"].lower() and "wyk" in c["name"].lower()), None)

    if dana and michaela:
        sb.table("candidates").update({
            "category": "couple",
            "couple_partner_id": michaela["id"]
        }).eq("id", dana["id"]).execute()
        sb.table("candidates").update({
            "category": "couple",
            "couple_partner_id": dana["id"]
        }).eq("id", michaela["id"]).execute()
        print(f"  Linkeados: {dana['name']} ↔ {michaela['name']}")
    else:
        print(f"  Dana: {'encontrada' if dana else 'NO encontrada'}")
        print(f"  Michaela: {'encontrada' if michaela else 'NO encontrada'}")

    # --- Final summary ---
    final = get_all_candidates()
    couples = [c for c in final if c["category"] == "couple"]
    solos   = [c for c in final if c["category"] == "solo"]
    print(f"\n=== Resultado final ===")
    print(f"  Parejas (personas): {len(couples)} → {len(couples)//2} tarjetas")
    print(f"  Solos: {len(solos)}")
    print(f"  Total candidatos: {len(final)}")

    if couples:
        print("\nParejas válidas:")
        shown = set()
        for c in couples:
            pid = c.get("couple_partner_id")
            pair = frozenset([c["id"], pid])
            if pair in shown or not pid:
                continue
            shown.add(pair)
            partner = by_id.get(pid) or next((x for x in final if x["id"] == pid), None)
            pname = partner["name"] if partner else "?"
            print(f"  ⇌ {c['name']} + {pname}")


if __name__ == "__main__":
    main()
