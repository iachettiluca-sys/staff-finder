#!/usr/bin/env python3
import sys
sys.path.insert(0, 'src')
from dotenv import load_dotenv; load_dotenv('.env')
from supabase_ops import get_client
sb = get_client()

res = sb.table('candidates').select('name,position,ai_score,pdf_text,bio').execute()
rows = res.data or []

no_cv  = [r for r in rows if not (r.get('pdf_text') or '').strip()]
has_cv = [r for r in rows if (r.get('pdf_text') or '').strip()]
chef   = [r for r in rows if r.get('position') == 'Chef']
host   = [r for r in rows if r.get('position') == 'Host']
unk    = [r for r in rows if r.get('position') not in ('Chef', 'Host')]

print(f"Total candidatos: {len(rows)}")
print(f"  Con texto de CV : {len(has_cv)}")
print(f"  Sin texto de CV : {len(no_cv)} (PDF escaneado o vacio)")
print(f"  Posicion Chef   : {len(chef)}")
print(f"  Posicion Host   : {len(host)}")
print(f"  Posicion descon.: {len(unk)}")
print()

print("=== SIN CV TEXT ===")
for r in no_cv:
    bio_ok = bool((r.get('bio') or '').strip())
    print(f"  [{r['position']}] {r['name']} | score={r['ai_score']} | bio={bio_ok}")

print()
print("=== SAMPLE CV TEXT (primeros 300 chars por candidato) ===")
for r in has_cv[:10]:
    snippet = (r.get('pdf_text') or '')[:300].replace('\n', ' ')
    print(f"  [{r['position']}] {r['name']}")
    print(f"    {snippet}")
    print()

print("=== TODOS LOS CHEF (verificar si estan bien catalogados) ===")
for r in chef:
    cv_len = len((r.get('pdf_text') or '').strip())
    bio_ok = bool((r.get('bio') or '').strip())
    snippet = (r.get('pdf_text') or '')[:200].replace('\n', ' ')
    print(f"  {r['name']} | score={r['ai_score']} | cv={cv_len}c | bio={bio_ok}")
    if snippet:
        print(f"    CV: {snippet}")
