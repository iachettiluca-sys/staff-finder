"""
age_nationality_extractor.py — Extrae edad y nacionalidad del CV de un candidato.

Lógica:
  - Edad: calcula desde fecha de nacimiento, o toma la edad mencionada explícitamente.
  - Nacionalidad: busca mención explícita en el CV; si no, infiere desde el código de área del teléfono.
  - Devuelve ISO 3166-1 alpha-2 (2 letras) para la nacionalidad.
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
    "name": "set_profile",
    "description": "Registra la edad y nacionalidad del candidato.",
    "input_schema": {
        "type": "object",
        "properties": {
            "age": {
                "type": "integer",
                "description": "Edad en años (entero), o null si no hay información.",
            },
            "nationality": {
                "type": "string",
                "description": (
                    "Código de país ISO 3166-1 alpha-2 (2 letras mayúsculas, ej: AR, CL, US, GB). "
                    "Null si no se puede determinar."
                ),
            },
        },
        "required": ["age", "nationality"],
    },
}

CURRENT_YEAR = 2026

def extract_age_nationality(cv_text: str, bio: str) -> dict:
    """
    Returns dict: {"age": int|None, "nationality": str|None}
    nationality es un código ISO 3166-1 alpha-2 de 2 letras (ej: "AR", "CL", "US").
    """
    combined = f"CV:\n{cv_text}\n\nPresentación:\n{bio}".strip()[:4000]
    if not combined or combined == "CV:\n\n\nPresentación:":
        return {"age": None, "nationality": None}

    prompt = f"""Del siguiente texto de CV y presentación del candidato, extraé:

1. EDAD: Si hay fecha de nacimiento, calculá la edad actual (año actual: {CURRENT_YEAR}).
   Si menciona su edad explícitamente (ej: "tengo 28 años", "28 años de edad"), usá esa.
   Si no hay información de edad, devolvé null.

2. NACIONALIDAD como código ISO 3166-1 alpha-2 (2 letras mayúsculas):
   - Si el CV menciona explícitamente la nacionalidad, ciudadanía o país de origen → usá ese país.
   - Si no dice la nacionalidad, analizá el número de teléfono y su código de área internacional para inferir el país.
     Ejemplos: +54 → AR, +56 → CL, +598 → UY, +55 → BR, +1 → US, +44 → GB, +33 → FR, +34 → ES, +27 → ZA, +64 → NZ, +61 → AU
   - Si no podés determinar, devolvé null.

TEXTO DEL CANDIDATO:
{combined}"""

    try:
        response = _get_client().messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=150,
            tools=[TOOL_SCHEMA],
            tool_choice={"type": "tool", "name": "set_profile"},
            messages=[{"role": "user", "content": prompt}],
        )
        for block in response.content:
            if block.type == "tool_use":
                age = block.input.get("age")
                nat = block.input.get("nationality")
                # Ignorar strings "null" que a veces devuelve el modelo
                if isinstance(age, str) and age.lower() == "null":
                    age = None
                if isinstance(nat, str) and nat.lower() == "null":
                    nat = None
                # Validar: edad entre 16 y 80, nacionalidad 2 letras
                if age is not None and not (16 <= int(age) <= 80):
                    age = None
                if nat is not None and len(str(nat)) != 2:
                    nat = None
                return {
                    "age": int(age) if age is not None else None,
                    "nationality": str(nat).upper() if nat else None,
                }
    except Exception as e:
        print(f"[age_nationality] Error: {e}")

    return {"age": None, "nationality": None}
