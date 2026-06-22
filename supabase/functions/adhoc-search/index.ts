/**
 * adhoc-search — Evalúa todos los candidatos contra un puesto personalizado.
 * Secrets requeridos: ANTHROPIC_API_KEY (Settings → Edge Functions → Secrets)
 * SUPABASE_URL y SUPABASE_SERVICE_ROLE_KEY se inyectan automáticamente.
 */

const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "authorization, x-client-info, apikey, content-type",
  "Access-Control-Allow-Methods": "POST, OPTIONS",
}

function json(body: unknown) {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json", ...CORS },
  })
}

Deno.serve(async (req: Request) => {
  if (req.method === "OPTIONS") return new Response(null, { headers: CORS })

  // 1. Parsear body
  let position_title = "", requirements = "", search_id = ""
  try {
    const b = await req.json()
    position_title = b.position_title ?? ""
    requirements   = b.requirements   ?? ""
    search_id      = b.search_id      ?? ""
  } catch (e) {
    return json({ error: `JSON inválido: ${e}` })
  }

  if (!position_title || !requirements || !search_id) {
    return json({ error: `Faltan campos. position_title="${position_title}" search_id="${search_id}"` })
  }

  // 2. Variables de entorno
  const SUPABASE_URL  = Deno.env.get("SUPABASE_URL")  ?? ""
  const SERVICE_KEY   = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY") ?? ""
  const ANTHROPIC_KEY = Deno.env.get("ANTHROPIC_API_KEY") ?? ""

  if (!SUPABASE_URL || !SERVICE_KEY) {
    return json({ error: "Variables SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY no disponibles" })
  }
  if (!ANTHROPIC_KEY) {
    return json({ error: "ANTHROPIC_API_KEY no configurada → Supabase Dashboard → Settings → Edge Functions → Secrets" })
  }

  // 3. Obtener candidatos
  let candidates: any[] = []
  try {
    const url =
      `${SUPABASE_URL}/rest/v1/candidates` +
      `?select=id,name,position,pdf_text,bio,ai_score,pdf_url,category,couple_partner_id` +
      `&search_id=eq.${encodeURIComponent(search_id)}` +
      `&status=neq.spam` +
      `&order=ai_score.desc.nullslast`

    console.log(`[adhoc-search] Fetching candidates, search_id=${search_id}`)
    const resp = await fetch(url, {
      headers: { "apikey": SERVICE_KEY, "Authorization": `Bearer ${SERVICE_KEY}` },
    })

    if (!resp.ok) {
      const txt = await resp.text()
      return json({ error: `DB error ${resp.status}: ${txt}` })
    }
    candidates = await resp.json()
    console.log(`[adhoc-search] ${candidates.length} candidatos encontrados`)
  } catch (e) {
    return json({ error: `Excepción consultando DB: ${e}` })
  }

  if (!Array.isArray(candidates) || candidates.length === 0) {
    return json({ error: `0 candidatos activos para search_id=${search_id}. ¿El search_id es correcto?` })
  }

  // 4. Evaluar con Claude en batches de 10
  const scored: any[] = []
  let scoringError = ""

  try {
    const BATCH = 10
    for (let i = 0; i < candidates.length; i += BATCH) {
      const batch = candidates.slice(i, i + BATCH)
      const results = await Promise.all(batch.map(c =>
        scoreCandidate(c, position_title, requirements, ANTHROPIC_KEY)
      ))
      scored.push(...results)
      console.log(`[adhoc-search] Batch ${Math.floor(i/BATCH)+1}: ${results.length} evaluados`)
    }
  } catch (e) {
    scoringError = String(e)
    console.error(`[adhoc-search] Error en scoring loop: ${e}`)
  }

  if (scored.length === 0) {
    return json({
      error: `Se encontraron ${candidates.length} candidatos pero el scoring falló. Error: ${scoringError || "desconocido"}`,
    })
  }

  scored.sort((a, b) => (b.custom_score ?? 0) - (a.custom_score ?? 0))
  console.log(`[adhoc-search] Listo. ${scored.length} resultados, top score: ${scored[0]?.custom_score}`)
  return json({ results: scored })
})

async function scoreCandidate(
  candidate: any,
  position_title: string,
  requirements: string,
  apiKey: string,
): Promise<any> {
  const cvText  = String(candidate.pdf_text ?? "").slice(0, 3000)
  const bio     = String(candidate.bio ?? "").slice(0, 800)
  const content = [
    cvText && `CV:\n${cvText}`,
    bio    && `Presentación:\n${bio}`,
  ].filter(Boolean).join("\n\n")

  if (!content.trim()) {
    return { ...candidate, custom_score: 0, custom_summary: "Sin CV ni bio para evaluar." }
  }

  const prompt =
    `Sos un reclutador experto para lodges de lujo en la Patagonia Argentina (temporada Nov-Apr).
Evaluá al candidato "${candidate.name}" para el puesto: ${position_title}

REQUISITOS:
${requirements}

CANDIDATO:
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
          input_schema: {
            type: "object",
            properties: {
              score:   { type: "integer", minimum: 0, maximum: 100 },
              summary: { type: "string" },
            },
            required: ["score", "summary"],
          },
        }],
        tool_choice: { type: "tool", name: "score" },
        messages: [{ role: "user", content: prompt }],
      }),
    })

    if (!resp.ok) {
      const txt = await resp.text()
      console.error(`[adhoc-search] Anthropic ${resp.status} for ${candidate.name}: ${txt.slice(0,200)}`)
      return { ...candidate, custom_score: 0, custom_summary: `Error API (${resp.status})` }
    }

    const data = await resp.json()
    const tool = data.content?.find((c: any) => c.type === "tool_use")
    if (tool?.input) {
      return { ...candidate, custom_score: tool.input.score ?? 0, custom_summary: tool.input.summary ?? "" }
    }

    console.error(`[adhoc-search] Sin tool_use en respuesta para ${candidate.name}`)
    return { ...candidate, custom_score: 0, custom_summary: "Sin respuesta de IA." }

  } catch (e) {
    console.error(`[adhoc-search] Excepción evaluando ${candidate.name}: ${e}`)
    return { ...candidate, custom_score: 0, custom_summary: `Error: ${e}` }
  }
}
