#!/usr/bin/env python3
"""
fix_positions.py — Re-detecta posición de TODOS los candidatos usando keywords del CV.
No usa Claude: reglas deterministas basadas en el título del CV y experiencia laboral.
Luego limpia duplicados residuales y candidatos inválidos.
"""
from __future__ import annotations
import sys, re
from pathlib import Path

root = Path(__file__).resolve().parent
sys.path.insert(0, str(root / "src"))
from dotenv import load_dotenv; load_dotenv(root / ".env")
from supabase_ops import get_client
sb = get_client()

# ── Keywords por posición ────────────────────────────────────────────────────

CHEF_STRONG = [
    r'\bchef\b', r'\bcocinero\b', r'\bcocinera\b', r'\bsous.?chef\b',
    r'\bhead.?chef\b', r'\bexecutive.?chef\b', r'\bcook\b', r'\bcuisine\b',
    r'\bculinary\b', r'\bcocina\b', r'\bgastronomia\b', r'\bgastronomic',
    r'\bpastelero\b', r'\bpastelera\b', r'\bpastry\b',
    r'\bcommi\b', r'\bbrigade\b', r'\bkitchen\b', r'\bmenú\b', r'\bmenu\b',
    r'\brecetas\b', r'\brecipe', r'\bfood.?prep', r'\bcoccion\b',
    r'\bayudante.?de.?cocina\b', r'\bartesano.?gastro', r'\bcocina.?patagonica\b',
]

HOST_STRONG = [
    r'\bcamarero\b', r'\bcamarera\b', r'\bmozo\b', r'\bmoza\b',
    r'\bmesero\b', r'\bmesera\b', r'\bwaiter\b', r'\bwaitress\b',
    r'\brecepcionista\b', r'\breception', r'\bbartender\b', r'\bbarman\b',
    r'\bsommelier\b', r'\bsommelière\b',
    r'\bhost\b', r'\banfitrion', r'\banfitriona\b',
    r'\bhospitality\b', r'\bhospitalidad\b',
    r'\batencion.?al.?cliente\b', r'\batención.?al.?cliente\b',
    r'\bcustomer.?service\b', r'\bservicio.?al.?cliente\b',
    r'\bfront.?of.?house\b', r'\bfoh\b',
    r'\bturismo\b', r'\bhoteleria\b', r'\bhotelería\b',
    r'\brelaciones.?publicas\b', r'\brelaciones.?públicas\b',
    r'\bsales\b', r'\bventas\b',
    r'\bjefe.?de.?salon\b', r'\bjefa.?de.?salon\b',
    r'\bsalon\b', r'\bsalón\b',
    r'\bguia.?de.?turismo\b', r'\bguide\b',
    r'\beventos\b', r'\bevents\b',
]

def count_keywords(text: str, patterns: list) -> int:
    t = text.lower()
    return sum(1 for p in patterns if re.search(p, t, re.IGNORECASE))

def detect_position(cv_text: str, bio: str, current_pos: str) -> tuple[str, str]:
    """Returns (new_position, reason)."""
    combined = f"{cv_text} {bio}"

    chef_hits  = count_keywords(combined, CHEF_STRONG)
    host_hits  = count_keywords(combined, HOST_STRONG)

    # CV header / title line (first 300 chars) gets double weight
    header = combined[:300]
    chef_hits  += count_keywords(header, CHEF_STRONG)
    host_hits  += count_keywords(header, HOST_STRONG)

    if chef_hits == 0 and host_hits == 0:
        return current_pos, "sin keywords — mantengo"

    if chef_hits > host_hits:
        return "Chef", f"chef={chef_hits} > host={host_hits}"
    elif host_hits > chef_hits:
        return "Host", f"host={host_hits} > chef={chef_hits}"
    else:
        # Tie — use current position, or "Host" if unknown
        if current_pos in ("Chef", "Host"):
            return current_pos, f"empate ({chef_hits}={host_hits}) — mantengo"
        return "Host", f"empate ({chef_hits}={host_hits}) — default Host"


def main():
    res = sb.table("candidates").select(
        "id,name,position,pdf_text,bio,ai_score"
    ).execute()
    rows = res.data or []

    # ── 1. Detectar y corregir posiciones ──────────────────────────────────
    print("=== CORRECCIÓN DE POSICIONES ===\n")
    changes = []
    for r in rows:
        cv   = r.get("pdf_text") or ""
        bio  = r.get("bio") or ""
        cur  = r.get("position") or "unknown"
        new_pos, reason = detect_position(cv, bio, cur)
        if new_pos != cur:
            changes.append((r["id"], r["name"], cur, new_pos, reason))

    for cid, name, old, new, reason in changes:
        print(f"  {name}: {old} -> {new}  ({reason})")
        sb.table("candidates").update({"position": new}).eq("id", cid).execute()

    print(f"\n{len(changes)} posiciones corregidas.")

    # ── 2. Limpiar duplicados residuales (misma longitud de CV) ────────────
    print("\n=== DUPLICADOS RESIDUALES (mismo cv_text) ===\n")

    # Reload
    res2 = sb.table("candidates").select(
        "id,name,position,pdf_text,bio,ai_score,couple_partner_id"
    ).execute()
    rows2 = res2.data or []

    # Build set of couple pairs (never dedup partners against each other)
    couple_pairs: set[frozenset] = set()
    for r in rows2:
        pid = r.get("couple_partner_id")
        if pid:
            couple_pairs.add(frozenset([r["id"], pid]))

    # Group by cv_text fingerprint (first 400 chars, stripped)
    from collections import defaultdict
    cv_groups: dict[str, list] = defaultdict(list)
    for r in rows2:
        fp = (r.get("pdf_text") or "").strip()[:400]
        if fp:  # only group non-empty CVs
            cv_groups[fp].append(r)

    to_delete_dup = []
    for fp, group in cv_groups.items():
        if len(group) < 2:
            continue
        # If all members of this group are couple-partners → skip (shared CV file is normal)
        ids_in_group = {r["id"] for r in group}
        if any(couple_pairs & {frozenset([a, b])
               for a in ids_in_group for b in ids_in_group if a != b}):
            print(f"  SKIP (pareja): {', '.join(r['name'] for r in group)}")
            continue
        # Keep the one with best data
        def _key(c):
            return (
                1 if c.get("couple_partner_id") else 0,
                1 if (c.get("bio") or "").strip() else 0,
                len(c.get("pdf_text") or ""),
                c.get("ai_score") or 0,
            )
        keeper = max(group, key=_key)
        dupes  = [c for c in group if c["id"] != keeper["id"]]
        to_delete_dup.extend(d["id"] for d in dupes)
        print(f"  KEEP: {keeper['name']} (score={keeper['ai_score']})")
        for d in dupes:
            print(f"  DEL:  {d['name']} (score={d['ai_score']})")
        print()

    if to_delete_dup:
        sb.table("candidates").update({"couple_partner_id": None}).in_(
            "couple_partner_id", to_delete_dup
        ).execute()
        sb.table("candidates").update({"couple_partner_id": None}).in_(
            "id", to_delete_dup
        ).execute()
        for i in range(0, len(to_delete_dup), 50):
            sb.table("candidates").delete().in_("id", to_delete_dup[i:i+50]).execute()
        print(f"{len(to_delete_dup)} duplicados por CV eliminados.")
    else:
        print("No hay duplicados por CV.")

    # ── 3. Limpiar registros inválidos (empresa/email/sin nombre real) ──────
    print("\n=== REGISTROS INVÁLIDOS ===\n")
    INVALID_NAMES = [
        "werben hr",
        "k.jessy@web.de",
        # "el caldero" and "cucina vagante" → dedup by CV above handles them
    ]
    res3 = sb.table("candidates").select("id,name").execute()
    for r in res3.data or []:
        name_low = r["name"].lower()
        if any(inv in name_low for inv in INVALID_NAMES):
            print(f"  Eliminando inválido: {r['name']}")
            sb.table("candidates").update({"couple_partner_id": None}).eq("id", r["id"]).execute()
            sb.table("candidates").delete().eq("id", r["id"]).execute()

    # ── 4. Resumen final ────────────────────────────────────────────────────
    final = sb.table("candidates").select("id,name,position").execute().data or []
    chef_n = sum(1 for r in final if r["position"] == "Chef")
    host_n = sum(1 for r in final if r["position"] == "Host")
    print(f"\n=== RESULTADO FINAL ===")
    print(f"  Total: {len(final)}  |  Chef: {chef_n}  |  Host: {host_n}")
    print()
    print("HOST:")
    for r in sorted([r for r in final if r["position"] == "Host"], key=lambda x: x["name"]):
        print(f"  {r['name']}")
    print("\nCHEF:")
    for r in sorted([r for r in final if r["position"] == "Chef"], key=lambda x: x["name"]):
        print(f"  {r['name']}")


if __name__ == "__main__":
    main()
