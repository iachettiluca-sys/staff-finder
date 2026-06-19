"""
name_extractor.py — Extrae nombre real y puesto de un CV usando Claude.
"""
from __future__ import annotations
import os, re
import anthropic

_client = None

def _get_client():
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    return _client


def _clean_filename_hint(stem: str) -> str:
    """Strip common CV words from filename to get a likely candidate name."""
    noise = r'\b(cv|curriculum|vitae|resume|hoja\s*de\s*vida|private\s*chef|english|espa[nñ]ol|' \
            r'ing|mba|don|lic|dr|sr|sra|mr|ms|mrs|chef|host|hostess|waiter|cook|' \
            r'arg|patagonia|2024|2025|2026|[a-z]?\d+|copy|final|v\d|update[d]?|new)\b'
    cleaned = re.sub(noise, ' ', stem, flags=re.IGNORECASE)
    cleaned = re.sub(r'[_\-\(\)\[\]\.&]+', ' ', cleaned)
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    return cleaned if len(cleaned) > 2 else stem


TOOL_SCHEMA = {
    "name": "report_candidate_info",
    "description": "Reporta el nombre completo y el puesto al que aplica el candidato.",
    "input_schema": {
        "type": "object",
        "properties": {
            "full_name": {
                "type": "string",
                "description": (
                    "Nombre completo del candidato. Buscalo en todo el CV — "
                    "puede estar en el encabezado, firma, pie de página o en cualquier parte. "
                    "Si el CV está vacío o ilegible, intentá extraerlo del nombre del archivo. "
                    "Solo devolvé 'Desconocido' si no hay absolutamente ninguna pista."
                ),
            },
            "position": {
                "type": "string",
                "enum": ["Chef", "Host", "unknown"],
                "description": (
                    "Puesto al que aplica. 'Host' incluye hostess, anfitrión/a, hospitalidad, "
                    "turismo, lodge, front of house. 'Chef' incluye cocinero/a, gastronomía, "
                    "cocina. Si no está claro, 'unknown'."
                ),
            },
        },
        "required": ["full_name", "position"],
    },
}


def extract_name_and_position(cv_text: str, bio: str, fallback_name: str) -> dict:
    """
    Extrae nombre completo y puesto desde el texto del CV y la bio del mail.
    Retorna {"full_name": str, "position": str}.
    Nunca lanza excepciones.
    """
    filename_hint = _clean_filename_hint(fallback_name)

    try:
        content = (
            f"Nombre del archivo (puede contener el nombre del candidato): {filename_hint}\n\n"
            f"TEXTO DEL CV (leé todo el texto buscando nombre, apellido, experiencia y puesto):\n"
            f"{cv_text[:6000] if cv_text else '(CV vacío o no legible — usá el nombre del archivo como referencia)'}\n\n"
            f"PRESENTACIÓN / BIO (del cuerpo del mail):\n"
            f"{bio[:1000] if bio else '(sin bio)'}"
        )

        response = _get_client().messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=300,
            tools=[TOOL_SCHEMA],
            tool_choice={"type": "tool", "name": "report_candidate_info"},
            messages=[{"role": "user", "content": content}],
        )
        for block in response.content:
            if block.type == "tool_use" and block.name == "report_candidate_info":
                name = block.input.get("full_name", "").strip()
                # If Claude returns Desconocido but we have a clean filename hint, prefer that
                if not name or name.lower() in ("desconocido", "unknown", ""):
                    name = filename_hint if len(filename_hint) > 3 else fallback_name
                position = block.input.get("position", "unknown")
                return {"full_name": name, "position": position}
    except Exception as e:
        print(f"[name_extractor] Error: {e}")

    # Last resort: use cleaned filename
    return {"full_name": filename_hint if len(filename_hint) > 3 else fallback_name, "position": "unknown"}
