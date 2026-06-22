/**
 * adhoc-search — Evalúa todos los candidatos contra un puesto personalizado.
 *
 * Secrets requeridos en Supabase (Settings → Edge Functions → Secrets):
 *   ANTHROPIC_API_KEY = sk-ant-...
 *
 * SUPABASE_URL y SUPABASE_SERVICE_ROLE_KEY se inyectan automáticamente.
 */

const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "authorization, x-client-info, apikey, content-type",
  "Access-Control-Allow-Methods": "POST, OPTIONS",
}

// Siempre devuelve HTTP 200 — los errores van dentro del JSON como { error: "..." }
// Esto evita que el cliente Supabase oculte el mensaje real del error.
function ok(body: unknown) {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json", ...CORS },
  })
}

Deno.serve(async (req: Request) => {
  if (req.method === "OPTIONS") {
    return new Response(null, { headers: CORS })
  }

  // ── Leer body ────────────────────────────────────────────────────────────
  let position_title: string, requirements: string, search_id: string
  try {
    const body = await req.json()
    position_title = body.position_title
    requirements   = body.requirements
    search_id      = body.search_id
  } catch {
    return ok({ error: "Body inválido — se esperaba JSON con position_title, requirements, search_id" })
  }

  if (!position_title || !requirements || !search_id) {
    return ok({ error: `Faltan campos. Recibido: position_title="${position_title}", search_id="${search_id}"` })
  }

  // ── Env vars ──────────────────────────────────────────────────────────────
  const SUPABASE_URL   = Deno.env.get("SUPABASE_URL")   ?? ""
  const SERVICE_KEY    = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY") ?? ""
  const ANTHROPIC_KEY  = Deno.env.get("ANTHROPIC_API_KEY") ?? ""

  if (!SUPABASE_URL || !SERVICE_KEY) {
    return ok({ error: "Variables de entorno de Supabase no disponibles (SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY)" })
  }
  if (!ANTHROPIC_KEY) {
    return ok({ error: "ANTHROPIC_API_KEY no configurada. Ir a Supabase Dashboard → Settings → Edge Functions → Secrets y agregar ANTHROPIC_API_KEY." })
  }

  // ── Fetch candidatos ──────────────────────────────────────────────────────
  let candidates: any[]
  try {
    const url =
      `${SUPABASE_URL}/rest/v1/candidates` +
      `?select=id,name,position,pdf_text,bio,ai_score,pdf_url,category,couple_partner_id` +
      `&search_id=eq.${search_id}` +
      `&status=neq.spam` +
      `&order=ai_score.desc.nullslast`

    const dbResp = await fetch(url, {
      headers: {
        "apikey": SERVICE_KEY,
        "Authorization": `Bearer ${SERVICE_KEY}`,
      },
    })

    if (!dbResp.ok) {
      const err = await dbResp.text()
      return ok({ error: `Error consultando DB (${dbResp.status}): ${err}` })
    }

    candidates = await dbResp.json()
  } catch (e) {
    return ok({ error: `Excepción al consultar DB: ${String(e)}` })
  }

  if (!candidates.length) {
    return ok({ error: `No se encontraron candidatos activos para search_id=${search_id}` })
  }

  // ── Evaluar con Claude en batches de 10 ──────────────────────────────────
  const BATCH = 10
  const scored: any[] = []

  for (let i = 0; i < candidates.length; i += BATCH) {
    const batch = candidates.slice(i, i + BATCH)
    const results = await Promise.all(
      batch.map((c) => scoreCandidate(c, position_title, requirements, ANTHROPIC_KEY)),
    )
    scored.push(...results)
  }

  scored.sort((a, b) => (b.custom_score ?? 0) - (a.custom_score ?? 0))
  return ok({ results: scored })
})

async function scoreCandidate(
  candidate: any,
  position_title: string,
  requirements: string,
  apiKey: string,
): Promise<any> {
  const cvText  = (candidate.pdf_text ?? "").slice(0, 3000)
  const bio     = (candidate.bio ?? "").slice(0, 800)
  const content = [
    cvText && `CV:\n${cvText}`,
    bio    && `Presentación:\n${bio}`,
  ].filter(Boolean).join("\n\n")

  if (!content.trim()) {
    return { ...candidate, custom_score: 0, custom_summary: "Sin CV ni bio disponible para evaluar." }
  }

  const prompt =
    `Sos un reclutador experto para lodges de lujo en la Patagonia Argentina (temporada Nov-Apr).
Evaluá al candidato "${candidate.name}" para el puesto: ${position_title}

REQUISITOS DEL PUESTO:
${requirements}

INFORMACIÓN DEL CANDIDATO:
${content}`

  try {
    const resp = await fetch("https://api.anthropic.com/v1/messages", {
      method: "POST",
      headers: {
        "x-api-key": apiKey,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
      },
      body: JSON.stringify({
        model: "claude-haiku-4-5-20251001",
        max_tokens: 300,
        tools: [{
          name: "score",
          description: "Puntuar al candidato para el puesto",
          input_schema: {
            type: "object",
            properties: {
              score:   { type: "integer", minimum: 0, maximum: 100 },
              summary: { type: "string",  description: "2-3 oraciones sobre el fit del candidato" },
            },
            required: ["score", "summary"],
          },
        }],
        tool_choice: { type: "tool", name: "score" },
        messages: [{ role: "user", content: prompt }],
      }),
    })

    if (!resp.ok) {
      const errText = await resp.text()
      console.error(`Anthropic error for ${candidate.name}: ${resp.status} ${errText}`)
      return { ...candidate, custom_score: 0, custom_summary: `Error Anthropic (${resp.status})` }
    }

    const data = await resp.json()
    const toolUse = data.content?.find((c: any) => c.type === "tool_use")
    if (toolUse?.input) {
      return {
        ...candidate,
        custom_score:   toolUse.input.score   ?? 0,
        custom_summary: toolUse.input.summary ?? "",
      }
    }
  } catch (e) {
    console.error(`Excepción evaluando ${candidate.name}:`, e)
  }

  return { ...candidate, custom_score: 0, custom_summary: "Error al evaluar." }
}
