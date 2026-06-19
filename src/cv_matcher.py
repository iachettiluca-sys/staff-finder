"""
cv_matcher.py — Llama a Claude API para puntuar un candidato contra los requisitos del puesto.
"""
from __future__ import annotations
import os, json
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
                "description": "Resumen de 2-3 oraciones en español explicando el puntaje.",
            },
            "strengths": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Lista de puntos fuertes del candidato para este puesto.",
            },
            "gaps": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Lista de aspectos que le faltan o no cumplen los requisitos.",
            },
        },
        "required": ["score", "summary", "strengths", "gaps"],
    },
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
    Retorna dict con score, summary, strengths, gaps.
    Nunca lanza excepciones.
    """
    try:
        if is_couple and partner_name:
            candidate_section = (
                f"CANDIDATO 1 — {candidate_name}\n"
                f"CV:\n{cv_text or '(sin CV)'}\n\n"
                f"CANDIDATO 2 — {partner_name}\n"
                f"CV:\n{partner_cv_text or '(sin CV)'}\n"
            )
            candidate_label = f"la pareja {candidate_name} y {partner_name}"
        else:
            candidate_section = f"CANDIDATO — {candidate_name}\nCV:\n{cv_text or '(sin CV)'}"
            candidate_label = candidate_name

        user_content = f"""Analizá la compatibilidad de {candidate_label} con el siguiente puesto:

PUESTO: {position_title}
REQUISITOS:
{position_requirements}

---
{candidate_section}
---
BIO / PRESENTACIÓN (del cuerpo del mail):
{bio or '(sin bio)'}

Puntuá del 0 al 100 qué tan bien encajan con el puesto y usá la herramienta report_match para reportar el resultado en español."""

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
