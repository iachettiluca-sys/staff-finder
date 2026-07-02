"""
cv_analyzer.py — Análisis completo de un CV en una sola llamada a Claude.

Reemplaza las 3 llamadas separadas a:
  - name_extractor.extract_name_and_position
  - cv_matcher.match_cv
  - age_nationality_extractor.extract_age_nationality

Resultado: un único dict con todos los campos.
"""
from __future__ import annotations
import os, re, unicodedata
import anthropic

_client = None

def _get_client():
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    return _client


def _clean_filename_hint(stem: str) -> str:
    noise = (r'\b(cv|curriculum|vitae|resume|hoja\s*de\s*vida|private\s*chef|english|espa[nñ]ol|'
             r'ing|mba|don|lic|dr|sr|sra|mr|ms|mrs|chef|host|hostess|waiter|cook|'
             r'arg|patagonia|2024|2025|2026|[a-z]?\d+|copy|final|v\d|update[d]?|new)\b')
    cleaned = re.sub(noise, ' ', stem, flags=re.IGNORECASE)
    cleaned = re.sub(r'[_\-\(\)\[\]\.&]+', ' ', cleaned)
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    return cleaned if len(cleaned) > 2 else stem


TOOL_SCHEMA = {
    "name": "analyze_cv",
    "description": "Reporta el análisis completo del candidato: identidad, compatibilidad con el puesto y datos de perfil.",
    "input_schema": {
        "type": "object",
        "properties": {
            "full_name": {
                "type": "string",
                "description": (
                    "Nombre completo del candidato. Buscalo en todo el CV — encabezado, firma, pie de página. "
                    "Si el CV está vacío, intentá extraerlo del nombre del archivo. "
                    "Solo devolvé 'Desconocido' si no hay absolutamente ninguna pista."
                ),
            },
            "position": {
                "type": "string",
                "enum": ["Chef", "Host", "unknown"],
                "description": (
                    "'Chef' incluye cocinero/a, gastronomía, cocina. "
                    "'Host' incluye hostess, mozo/a, camarero/a, bartender, anfitrión/a, hospitalidad, turismo, lodge, front of house. "
                    "Si no está claro, 'unknown'."
                ),
            },
            "score": {
                "type": "integer",
                "description": "Puntaje de compatibilidad del 0 al 100 con el puesto identificado.",
            },
            "summary": {
                "type": "string",
                "description": "Resumen de 2-3 oraciones en español explicando el puntaje basado en lo que se pudo leer.",
            },
            "strengths": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Puntos fuertes del candidato para este puesto, extraídos del CV.",
            },
            "gaps": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Aspectos que le faltan o no cumplen los requisitos.",
            },
            "age": {
                "type": ["integer", "null"],
                "description": (
                    "Edad en años. Calculá desde la fecha de nacimiento si aparece (año actual: 2026). "
                    "Si menciona la edad explícitamente, usá esa. Null si no hay información."
                ),
            },
            "nationality": {
                "type": ["string", "null"],
                "description": (
                    "Código ISO 3166-1 alpha-2 (2 letras mayúsculas: AR, CL, US, GB, etc.). "
                    "Si no hay mención explícita, inferí desde el código de área del teléfono "
                    "(+54→AR, +56→CL, +598→UY, +55→BR, +1→US, +44→GB, +33→FR, +34→ES). "
                    "Null si no se puede determinar."
                ),
            },
        },
        "required": ["full_name", "position", "score", "summary", "strengths", "gaps", "age", "nationality"],
    },
}

_FALLBACK = {
    "full_name": "Desconocido",
    "position": "unknown",
    "score": 0,
    "summary": "Error al analizar el CV.",
    "strengths": [],
    "gaps": [],
    "age": None,
    "nationality": None,
}

_UNREADABLE_SUMMARY = "CV no legible (posiblemente PDF escaneado sin capa de texto). Requiere revisión manual."


def analyze_cv(
    cv_text: str,
    bio: str,
    filename_hint: str,
    positions: list[dict],
    is_couple: bool = False,
    partner_name: str = "",
    partner_cv_text: str = "",
) -> dict:
    """
    Analiza un CV completo en una sola llamada a Claude.

    Retorna dict con: full_name, position, score, summary, strengths, gaps, age, nationality.
    Nunca lanza excepciones.

    Args:
        cv_text: Texto extraído del PDF del candidato.
        bio: Cuerpo del mail (presentación del candidato).
        filename_hint: Nombre del archivo sin extensión (pista para el nombre).
        positions: Lista de dicts {title, requirements} de config.yaml.
        is_couple: True si forma pareja con otro candidato.
        partner_name: Nombre del partner (solo si is_couple).
        partner_cv_text: Texto del CV del partner (solo si is_couple).
    """
    hint = _clean_filename_hint(filename_hint)
    has_content = bool(cv_text.strip() or bio.strip())

    # Sin contenido legible: devolvemos fallback con nombre del archivo
    if not has_content and not partner_cv_text.strip():
        return {
            **_FALLBACK,
            "full_name": hint if len(hint) > 2 else filename_hint,
            "summary": _UNREADABLE_SUMMARY,
            "gaps": ["PDF sin texto extraíble — revisar manualmente"],
        }

    # Armar sección de requisitos de puestos
    pos_section = "\n\n".join(
        f"REQUISITOS — {p['title']}:\n{p['requirements']}"
        for p in positions
    )

    host_definition = (
        "HOST en este contexto es un rol de servicio de lujo, 100% unisex. "
        "Incluye: mozo/a, camarero/a, recepcionista, bartender, sommelier, "
        "guía de actividades, coordinador/a de huéspedes, anfitrión/a. "
        "Cualquier experiencia en atención al cliente, hotelería, turismo, "
        "ventas, relaciones públicas o front of house cuenta positivamente."
    )

    # Armar sección del candidato
    if is_couple and partner_name and partner_cv_text.strip():
        cv1 = cv_text.strip() or "(CV no extraíble)"
        cv2 = partner_cv_text.strip() or "(CV no extraíble)"
        candidate_section = (
            f"CANDIDATO (este CV):\n{cv1[:5000]}\n\n"
            f"PARTNER — {partner_name}:\n{cv2[:3000]}"
        )
        couple_note = f"Este candidato postula en pareja con {partner_name}."
    else:
        cv_content = cv_text.strip() or "(CV no extraíble — solo bio disponible)"
        candidate_section = f"CV COMPLETO:\n{cv_content[:8000]}"
        couple_note = ""

    bio_section = bio.strip() if bio.strip() else "(sin presentación)"

    prompt = f"""Sos un reclutador experto para lodges de lujo en la Patagonia Argentina (temporada Nov-Apr).
Analizá el siguiente candidato y completá el análisis con todos los campos requeridos.

NOMBRE DEL ARCHIVO (pista para el nombre): {hint}
{couple_note}

{pos_section}

{host_definition}

---
{candidate_section}

---
BIO / PRESENTACIÓN del mail:
{bio_section}
---

INSTRUCCIONES:
- Extraé el nombre completo del candidato del CV (encabezado, firma, donde sea).
- Determiná el puesto al que aplica (Chef o Host) basándote en el CV y la bio.
- Puntuá del 0 al 100 la compatibilidad con los requisitos del puesto identificado.
- REGLA ABSOLUTA: el género, sexo, nombre u origen NUNCA son criterio de evaluación. Evaluá ÚNICAMENTE experiencia, habilidades e idiomas.
- Ser argentino/a suma hasta 15 puntos. Vivir en la Patagonia suma hasta 10 puntos. Experiencia en lodges suma hasta 10 puntos.
- Si no encaja para su puesto pero sí para el otro, mencionalo en el resumen.
- Extraé edad y nacionalidad si están disponibles en el texto.
- Respondé siempre en español."""

    try:
        response = _get_client().messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1024,
            tools=[TOOL_SCHEMA],
            tool_choice={"type": "tool", "name": "analyze_cv"},
            messages=[{"role": "user", "content": prompt}],
        )

        for block in response.content:
            if block.type == "tool_use" and block.name == "analyze_cv":
                inp = block.input

                name = str(inp.get("full_name", "")).strip()
                if not name or name.lower() in ("desconocido", "unknown", ""):
                    name = hint if len(hint) > 2 else filename_hint

                age = inp.get("age")
                if isinstance(age, str) and age.lower() == "null":
                    age = None
                if age is not None:
                    try:
                        age = int(age)
                        if not (16 <= age <= 80):
                            age = None
                    except (ValueError, TypeError):
                        age = None

                nat = inp.get("nationality")
                if isinstance(nat, str) and nat.lower() == "null":
                    nat = None
                if nat is not None:
                    nat = str(nat).upper()
                    if len(nat) != 2:
                        nat = None

                return {
                    "full_name": name,
                    "position": inp.get("position", "unknown"),
                    "score": max(0, min(100, int(inp.get("score", 0)))),
                    "summary": str(inp.get("summary", "")),
                    "strengths": list(inp.get("strengths", [])),
                    "gaps": list(inp.get("gaps", [])),
                    "age": age,
                    "nationality": nat,
                }

    except Exception as e:
        print(f"[cv_analyzer] Error: {e}")

    return {**_FALLBACK, "full_name": hint if len(hint) > 2 else filename_hint}
