// TropeFinder "embed" Edge Function: turns text into a 384-number meaning vector with the
// gte-small model that Supabase runs built in (no outside AI service, no extra cost).
//
// POST {"input": "a regular guy gets powers in London"}      -> {"model", "embeddings": [[...384]]}
// POST {"input": ["text one", "text two", ...]}  (up to 8)   -> one vector per text
//
// Used by the search page (a visitor's typed search) and by the pipeline (book and trope
// texts). It reads and stores nothing. Limits keep it cheap: at most 8 texts per call and
// 2,000 characters per text (the model only reads about the first 512 words-pieces anyway).

const MODEL = "gte-small";
const MAX_TEXTS = 8;
const MAX_CHARS = 2000;

const session = new Supabase.ai.Session(MODEL);

const cors = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "authorization, apikey, content-type, x-client-info",
  "Access-Control-Allow-Methods": "POST, OPTIONS",
};

function reply(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { ...cors, "Content-Type": "application/json" } });
}

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") return new Response("ok", { headers: cors });
  if (req.method !== "POST") return reply({ error: "Use POST with {\"input\": text}" }, 405);
  if (!req.headers.get("apikey") && !req.headers.get("authorization")) {
    return reply({ error: "Missing apikey header" }, 401);
  }

  let input: unknown;
  try {
    input = (await req.json()).input;
  } catch {
    return reply({ error: "Body must be JSON: {\"input\": text or list of texts}" }, 400);
  }
  const texts = typeof input === "string" ? [input] : input;
  if (!Array.isArray(texts) || texts.length === 0 || texts.length > MAX_TEXTS ||
      texts.some((t) => typeof t !== "string" || !t.trim())) {
    return reply({ error: `input must be a non-empty text, or a list of 1 to ${MAX_TEXTS} texts` }, 400);
  }

  try {
    const embeddings: number[][] = [];
    for (const t of texts as string[]) {
      const out = await session.run(t.slice(0, MAX_CHARS), { mean_pool: true, normalize: true });
      embeddings.push(Array.from(out as ArrayLike<number>));
    }
    return reply({ model: MODEL, embeddings });
  } catch (err) {
    return reply({ error: `Embedding failed: ${String(err).slice(0, 200)}` }, 500);
  }
});
