"""
name_extractor.py — Extrae nombre real y puesto de un CV usando Claude.
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
    "name": "report_candidate_info",
    "description": "Reporta el nombre completo y el puesto al que aplica el candidato.",
    "input_schema": {
        "type": "object",
        "properties": {
            "full_name": {
                "type": "string",
                "description": "Nombre completo del candidato extraído del CV. Si no se encuentra, devolver 'Desconocido'.",
            },
            "position": {
                "type": "string",
                "enum": ["Chef", "Host", "unknown"],
                "description": "Puesto al que aplica. Host incluye hostess, anfitrión/a. Si no está claro, 'unknown'.",
            },
        },
        "required": ["full_name", "position"],
    },
}


def extract_name_and_position(cv_text: str, bio: str, fallback_name: str) -> dict:
    """
    Extrae nombre completo y puesto desde el texto del CV y la bio del mail.
    Retorna {"full_name": str, "position": str}
    Nunca lanza excepciones.
    """
    try:
        content = f"CV:\n{cv_text[:3000]}\n\nPresentación / bio:\n{bio[:1000]}"
        response = _get_client().messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=200,
            tools=[TOOL_SCHEMA],
            tool_choice={"type": "tool", "name": "report_candidate_info"},
            messages=[{"role": "user", "content": content}],
        )
        for block in response.content:
            if block.type == "tool_use" and block.name == "report_candidate_info":
                name = block.input.get("full_name", "").strip() or fallback_name
                position = block.input.get("position", "unknown")
                return {"full_name": name, "position": position}
    except Exception as e:
        print(f"[name_extractor] Error: {e}")
    return {"full_name": fallback_name, "position": "unknown"}
