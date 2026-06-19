"""
cv_matcher.py — Llama a Claude API para puntuar un candidato contra los requisitos del puesto.
"""
from __future__ import annotations
import os
import anthropic

_client = None

def _get_client():
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    return _client

TOOL_SCHEMA = {
    "name": "report_match",
    "description": "Reporta el resultado del análisis de compatibilidad del candidato con el puesto.",
    "input_schema": {
        "type": "object",
        "properties": {
            "score": {
                "type": "integer",
                "description": "Puntaje de compatibilidad del 0 al 100.",
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
        },
        "required": ["score", "summary", "strengths", "gaps"],
    },
}

_UNREADABLE = {
    "score": 0,
    "summary": "CV no legible (posiblemente PDF escaneado sin capa de texto). Requiere revisión manual.",
    "strengths": [],
    "gaps": ["PDF sin texto extraíble — revisar manualmente"],
}

_ERROR_RESULT = {"score": 0, "summary": "Error al analizar el CV.", "strengths": [], "gaps": []}


def match_cv(
    cv_text: str,
    bio: str,
    candidate_name: str,
    position_title: str,
    position_requirements: str,
    is_couple: bool = False,
    partner_name: str = "",
    partner_cv_text: str = "",
) -> dict:
    """
    Puntúa al candidato (o pareja) contra los requisitos del puesto.
    El CV es la fuente principal. La bio complementa si existe.
    Retorna dict con score, summary, strengths, gaps.
    Nunca lanza excepciones.
    """
    # Si no hay absolutamente nada que leer, marcar como no legible
    if not cv_text.strip() and not bio.strip() and not partner_cv_text.strip():
        return _UNREADABLE

    try:
        if is_couple and partner_name:
            cv1 = cv_text.strip() or "(CV no extraíble)"
            cv2 = partner_cv_text.strip() or "(CV no extraíble)"
            candidate_section = (
                f"CANDIDATO 1 — {candidate_name}\n"
                f"CV completo:\n{cv1[:5000]}\n\n"
                f"CANDIDATO 2 — {partner_name}\n"
                f"CV completo:\n{cv2[:5000]}\n"
            )
            candidate_label = f"la pareja {candidate_name} y {partner_name}"
        else:
            cv_content = cv_text.strip() or "(CV no extraíble — solo bio disponible)"
            candidate_section = (
                f"CANDIDATO — {candidate_name}\n"
                f"CV completo (leé todo, es la fuente principal):\n{cv_content[:8000]}"
            )
            candidate_label = candidate_name

        bio_section = bio.strip() if bio.strip() else "(sin bio)"

        # Definición de Host para este lodge de lujo
        host_definition = (
            "HOST en este contexto es un rol de servicio de lujo, 100% unisex. "
            "Incluye: mozo/a, camarero/a, recepcionista, bartender, sommelier, "
            "guía de actividades, coordinador/a de huéspedes, anfitrión/a. "
            "Cualquier experiencia en atención al cliente, hotelería, turismo, "
            "ventas, relaciones públicas o front of house cuenta positivamente para Host."
        ) if position_title == "Host" else ""

        user_content = f"""Analizá la compatibilidad de {candidate_label} con el puesto de {position_title}.

REQUISITOS DEL PUESTO:
{position_requirements}
{host_definition}

---
{candidate_section}

---
BIO / PRESENTACIÓN del mail (complementaria al CV):
{bio_section}
---

INSTRUCCIONES:
- El CV es tu ÚNICA fuente principal de evaluación. Leélo completo y extraé toda la información relevante: experiencia, idiomas, habilidades, formación.
- La bio del mail es solo información de contexto adicional, no reemplaza al CV.
- Si el CV está vacío pero hay bio, evaluá con lo que tenés y aclaralo.
- Puntuá del 0 al 100 qué tan bien encaja con el puesto basándote en el CV.
- REGLA ABSOLUTA — GÉNERO: El género, sexo, nombre, origen o cualquier característica personal del candidato NUNCA es criterio de evaluación. La palabra "Host" es unisex. Mozo, moza, camarero, camarera son equivalentes. Evaluá ÚNICAMENTE experiencia laboral, habilidades e idiomas. Si mencionás el género de alguna forma, la evaluación es inválida.
- Si el candidato no encaja para el puesto pero tiene perfil para el otro puesto disponible (Chef o Host), mencionalo brevemente en el resumen.
- Respondé siempre en español usando la herramienta report_match."""

        response = _get_client().messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1024,
            tools=[TOOL_SCHEMA],
            tool_choice={"type": "tool", "name": "report_match"},
            messages=[{"role": "user", "content": user_content}],
        )

        for block in response.content:
            if block.type == "tool_use" and block.name == "report_match":
                result = block.input
                return {
                    "score": max(0, min(100, int(result.get("score", 0)))),
                    "summary": str(result.get("summary", "")),
                    "strengths": list(result.get("strengths", [])),
                    "gaps": list(result.get("gaps", [])),
                }

        return _ERROR_RESULT

    except Exception as e:
        print(f"[cv_matcher] Error al llamar a Claude: {e}")
        return _ERROR_RESULT
