#!/usr/bin/env python3
"""
Prueba OCR descargando el PDF de un candidato con cv_text vacío desde Supabase Storage.
"""
import sys
from pathlib import Path
root = Path(__file__).resolve().parent
sys.path.insert(0, str(root / "src"))
from dotenv import load_dotenv; load_dotenv(root / ".env")

from supabase_ops import get_client
from pdf_extractor import extract_text_from_pdf

sb = get_client()

# Buscar candidatos sin texto de CV
res = sb.table("candidates").select("id,name,pdf_url,pdf_text,ai_score").execute()
sin_texto = [r for r in (res.data or []) if not (r.get("pdf_text") or "").strip() and r.get("pdf_url")]

if not sin_texto:
    print("No hay candidatos con PDF pero sin texto. Usando el primero de la lista...")
    sin_texto = [r for r in (res.data or []) if r.get("pdf_url")][:1]

print(f"Candidatos con PDF sin texto: {len(sin_texto)}")
for c in sin_texto:
    print(f"  {c['name']} | score={c['ai_score']} | url={c['pdf_url'][:60]}...")

# Probar con el primero
target = sin_texto[0]
print(f"\nProbando OCR con: {target['name']}")
print(f"URL: {target['pdf_url']}")

# Descargar el PDF desde Supabase Storage
import urllib.request
try:
    with urllib.request.urlopen(target["pdf_url"]) as resp:
        pdf_bytes = resp.read()
    print(f"PDF descargado: {len(pdf_bytes)} bytes")
except Exception as e:
    print(f"Error descargando: {e}")
    # Intentar con el segundo
    if len(sin_texto) > 1:
        target = sin_texto[1]
        print(f"\nIntentando con: {target['name']}")
        with urllib.request.urlopen(target["pdf_url"]) as resp:
            pdf_bytes = resp.read()

text = extract_text_from_pdf(pdf_bytes)
print(f"\n=== RESULTADO OCR ({len(text)} chars) ===")
print(text[:1500] if text else "(vacío — PDF no legible ni con OCR)")
