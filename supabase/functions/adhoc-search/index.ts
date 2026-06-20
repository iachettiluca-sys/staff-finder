/**
 * adhoc-search — Evalúa todos los candidatos contra un puesto personalizado.
 *
 * DEPLOYMENT (una sola vez):
 *   1. Supabase Dashboard → Edge Functions → "New function" → nombre: adhoc-search → pegar este código → Deploy
 *      O con CLI: supabase functions deploy adhoc-search
 *
 *   2. Agregar el secret:
 *      Dashboard → Settings → Edge Functions → Secrets → Add: ANTHROPIC_API_KEY = sk-ant-...
 *      O con CLI: supabase secrets set ANTHROPIC_API_KEY=sk-ant-...
 *
 * SUPABASE_URL y SUPABASE_SERVICE_ROLE_KEY se inyectan automáticamente.
 */

const corsHeaders = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "authorization, x-client-info, apikey, content-type",
  "Access-Control-Allow-Methods": "POST, OPTIONS",
}

Deno.serve(async (req: Request) => {
  if (req.method === "OPTIONS") {
    return new Response(null, { headers: corsHeaders })
  }

  try {
    const body = await req.json()
    const { position_title, requirements, search_id } = body

    if (!position_title || !requirements || !search_id) {
      return new Response(
        JSON.stringify({ error: "Faltan campos: position_title, requirements, search_id" }),
        { status: 400, headers: { "Content-Type": "application/json", ...corsHeaders } },
      )
    }

    const SUPABASE_URL = Deno.env.get("SUPABASE_URL")!
    const SERVICE_KEY  = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!
    const ANTHROPIC_KEY = Deno.env.get("ANTHROPIC_API_KEY") ?? ""

    if (!ANTHROPIC_KEY) {
      return new Response(
        JSON.stringify({ error: "ANTHROPIC_API_KEY no configurada en Supabase Secrets. Ver instrucciones en el archivo index.ts." }),
        { status: 500, headers: { "Content-Type": "application/json", ...corsHeaders } },
      )
    }

    // Fetch all active candidates via PostgREST
    const dbResp = await fetch(
      `${SUPABASE_URL}/rest/v1/candidates?` +
        `select=id,name,position,pdf_text,bio,ai_score,pdf_url,category,couple_partner_id` +
        `&search_id=eq.${encodeURIComponent(search_id)}` +
        `&status=neq.spam` +
        `&order=ai_score.desc.nullslast`,
      {
        headers: {
          "apikey": SERVICE_KEY,
          "Authorization": `Bearer ${SERVICE_KEY}`,
        },
      },
    )

    if (!dbResp.ok) {
      const err = await dbResp.text()
      return new Response(
        JSON.stringify({ error: `DB error: ${err}` }),
        { status: 500, headers: { "Content-Type": "application/json", ...corsHeaders } },
      )
    }

    const candidates: any[] = await dbResp.json()

    // Score all candidates in parallel batches of 10
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

    return new Response(
      JSON.stringify({ results: scored }),
      { headers: { "Content-Type": "application/json", ...corsHeaders } },
    )
  } catch (e) {
    return new Response(
      JSON.stringify({ error: String(e) }),
      { status: 500, headers: { "Content-Type": "application/json", ...corsHeaders } },
    )
  }
})

async function scoreCandidate(
  candidate: any,
  position_title: string,
  requirements: string,
  apiKey: string,
): Promise<any> {
  const cvText = (candidate.pdf_text ?? "").slice(0, 3000)
  const bio    = (candidate.bio ?? "").slice(0, 800)
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
        max_tokens: 256,
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
    console.error(`Error scoring ${candidate.name}:`, e)
  }

  return { ...candidate, custom_score: 0, custom_summary: "Error al evaluar." }
}
