/**
 * Cloudflare Worker — AI Chat API
 * Replaces the Flask/Render backend (api.py)
 *
 * Environment variables (set in Cloudflare dashboard or wrangler.toml [vars]):
 *   SITE_PASSWORD        — site access password
 *   GROQ_API_KEY         — comma-separated keys
 *   CEREBRAS_API_KEY     — comma-separated keys
 *   GEMINI_API_KEY       — comma-separated keys
 *   MISTRAL_API_KEY      — comma-separated keys
 *   ALLOWED_ORIGIN       — e.g. https://giamat13.github.io (default: *)
 */

// ─── Models ────────────────────────────────────────────────────
const ALL_MODELS = {
  "auto":                    { provider: "system" },
  "llama-3.1-8b-instant":    { provider: "groq",     api_id: "llama-3.1-8b-instant",                          heb: "Llama 3.1 8B",             category: "groq" },
  "llama-3.3-70b-versatile": { provider: "groq",     api_id: "llama-3.3-70b-versatile",                       heb: "Llama 3.3 70B",            category: "groq" },
  "llama-4-maverick":        { provider: "groq",     api_id: "meta-llama/llama-4-maverick-17b-128e-instruct", heb: "Llama 4 Maverick",         category: "groq" },
  "llama-4-scout":           { provider: "groq",     api_id: "meta-llama/llama-4-scout-17b-16e-instruct",     heb: "Llama 4 Scout",            category: "groq" },
  "kimi-k2":                 { provider: "groq",     api_id: "moonshotai/kimi-k2-instruct",                   heb: "Kimi K2",                  category: "groq" },
  "gpt-oss-120b":            { provider: "groq",     api_id: "openai/gpt-oss-120b",                           heb: "GPT OSS 120B",             category: "groq" },
  "gpt-oss-20b":             { provider: "groq",     api_id: "openai/gpt-oss-20b",                            heb: "GPT OSS 20B",              category: "groq" },
  "qwen3-32b":               { provider: "groq",     api_id: "qwen/qwen3-32b",                                heb: "Qwen 3 32B",               category: "groq" },
  "groq-compound":           { provider: "groq",     api_id: "groq/compound",                                 heb: "Groq Compound",            category: "groq" },
  "groq-compound-mini":      { provider: "groq",     api_id: "groq/compound-mini",                            heb: "Groq Compound Mini",       category: "groq" },
  "cerebras-llama3.1-8b":   { provider: "cerebras", api_id: "llama3.1-8b",                                   heb: "Llama 3.1 8B (Cerebras)", category: "cerebras" },
  "cerebras-gpt-oss-120b":  { provider: "cerebras", api_id: "gpt-oss-120b",                                  heb: "GPT OSS 120B (Cerebras)", category: "cerebras" },
  "cerebras-qwen3-235b":    { provider: "cerebras", api_id: "qwen3-235b",                                    heb: "Qwen3 235B (Cerebras)",   category: "cerebras" },
  "cerebras-zai-glm-4.7":  { provider: "cerebras", api_id: "zai-glm-4.7",                                   heb: "GLM 4.7 (Cerebras)",      category: "cerebras" },
  "gemini-2.5-flash-lite":  { provider: "gemini",   api_id: "gemini-2.5-flash-lite",                         heb: "Gemini 2.5 Flash-Lite",   category: "gemini" },
  "gemini-2.5-flash":       { provider: "gemini",   api_id: "gemini-2.5-flash",                              heb: "Gemini 2.5 Flash",        category: "gemini" },
  "gemini-2.5-pro":         { provider: "gemini",   api_id: "gemini-2.5-pro",                               heb: "Gemini 2.5 Pro",          category: "gemini" },
  "gemini-3-flash":         { provider: "gemini",   api_id: "gemini-3-flash-preview",                        heb: "Gemini 3 Flash",          category: "gemini" },
  "gemini-3-pro":           { provider: "gemini",   api_id: "gemini-3-pro-preview",                          heb: "Gemini 3 Pro",            category: "gemini" },
  "mistral-large":          { provider: "mistral",  api_id: "mistral-large-latest",                          heb: "Mistral Large 3",         category: "mistral" },
  "mistral-medium":         { provider: "mistral",  api_id: "mistral-medium-latest",                         heb: "Mistral Medium 3.1",      category: "mistral" },
  "mistral-small":          { provider: "mistral",  api_id: "mistral-small-latest",                          heb: "Mistral Small 3.2",       category: "mistral" },
  "ministral-14b":          { provider: "mistral",  api_id: "ministral-14b-latest",                          heb: "Ministral 3 14B",         category: "mistral" },
  "ministral-8b":           { provider: "mistral",  api_id: "ministral-8b-latest",                           heb: "Ministral 3 8B",          category: "mistral" },
  "ministral-3b":           { provider: "mistral",  api_id: "ministral-3b-latest",                           heb: "Ministral 3 3B",          category: "mistral" },
  "magistral-medium":       { provider: "mistral",  api_id: "magistral-medium-latest",                       heb: "Magistral Medium 1.2",    category: "mistral" },
  "magistral-small":        { provider: "mistral",  api_id: "magistral-small-latest",                        heb: "Magistral Small 1.2",     category: "mistral" },
  "codestral":              { provider: "mistral",  api_id: "codestral-latest",                              heb: "Codestral",               category: "mistral" },
  "devstral":               { provider: "mistral",  api_id: "devstral-latest",                               heb: "Devstral 2",              category: "mistral" },
  "mistral-nemo":           { provider: "mistral",  api_id: "open-mistral-nemo",                             heb: "Mistral Nemo 12B",        category: "mistral" },
};

const HEAVY_MODELS = new Set([
  "kimi-k2", "gpt-oss-120b", "llama-4-maverick", "groq-compound",
  "llama-4-scout", "qwen3-32b", "cerebras-gpt-oss-120b", "cerebras-qwen3-235b",
  "cerebras-zai-glm-4.7", "mistral-large", "magistral-medium", "magistral-small",
  "devstral", "gemini-2.5-pro", "gemini-3-flash", "gemini-3-pro",
]);

const PRIORITY_ORDER = [
  "groq-compound", "groq-compound-mini", "kimi-k2", "qwen3-32b",
  "llama-4-maverick", "llama-4-scout", "gpt-oss-120b", "gpt-oss-20b",
  "llama-3.3-70b-versatile", "llama-3.1-8b-instant",
  "cerebras-qwen3-235b", "cerebras-gpt-oss-120b", "cerebras-zai-glm-4.7", "cerebras-llama3.1-8b",
  "gemini-2.5-flash", "gemini-2.5-flash-lite", "gemini-2.5-pro", "gemini-3-flash", "gemini-3-pro",
  "mistral-large", "mistral-medium", "magistral-medium", "codestral", "devstral",
  "mistral-small", "mistral-nemo",
];

// ─── In-memory blocked models (lives for the duration of the Worker instance) ──
// NOTE: Cloudflare Workers may run in many instances simultaneously.
// For cross-instance blocking, use KV (see wrangler.toml comment).
// This simple in-memory map works well for single-instance / low-traffic bots.
const _blocked = new Map(); // key → unblockAt (ms) | -1 = permanent

function cleanBlocked() {
  const now = Date.now();
  for (const [k, v] of _blocked) {
    if (v !== -1 && v <= now) _blocked.delete(k);
  }
}

function isBlocked(modelName) {
  cleanBlocked();
  if (_blocked.has(modelName)) return true;
  const provider = ALL_MODELS[modelName]?.provider ?? "";
  return _blocked.has(`_provider_${provider}`);
}

function blockModel(modelName) {
  const provider = ALL_MODELS[modelName]?.provider ?? "groq";
  // Mistral & Gemini → provider-wide block (RPM shared); others → per-model
  const providerWide = provider === "mistral" || provider === "gemini";
  const key = providerWide ? `_provider_${provider}` : modelName;
  _blocked.set(key, Date.now() + 60_000); // 60 seconds
}

// ─── Auto model selection ───────────────────────────────────────
const KW_MAP = {
  "groq-compound-mini": ["מחיר","price","היום","today","עכשיו","now","חדשות","news","מזג אוויר","weather","מניה","stock","שעות פתיחה","opening hours","latest","recent"],
  "groq-compound":      ["חפש באינטרנט","search the web","מחקר עדכני","recent research","חדשות על","news about","עדכון על","update on"],
  "kimi-k2":            ["python","javascript","typescript","java","html","css","sql","bash","קוד","code","bug","debug","script","פונקצי","function","class","api","תכנות","אלגוריתם"],
  "qwen3-32b":          ["integral","derivative","matrix","אינטגרל","גזירה","מטריצה","משפט","הוכחה","proof","calculus","algebra","statistics","probability","הסתברות","חשבון"],
  "llama-4-scout":      ["מסמך ארוך","long document","קובץ ענק","huge file","כל הקוד","entire codebase","ספר שלם","full book"],
};

const CATEGORY_ROUTING = {
  INTERNET:     "groq-compound-mini",
  LONGDOC:      "llama-4-scout",
  CODE:         "kimi-k2",
  MATH:         "qwen3-32b",
  MULTILINGUAL: "llama-4-maverick",
  RESEARCH:     "gpt-oss-120b",
  FRONTIER:     "cerebras-gpt-oss-120b",
  WRITING:      "llama-3.3-70b-versatile",
  SIMPLE:       "llama-3.1-8b-instant",
};

function autoSelectModel(userMsg) {
  const lower = userMsg.toLowerCase();
  for (const [model, keywords] of Object.entries(KW_MAP)) {
    if (!isBlocked(model) && keywords.some(kw => lower.includes(kw))) return model;
  }
  // Fallback by priority
  for (const m of PRIORITY_ORDER) {
    if (!isBlocked(m) && ALL_MODELS[m]) return m;
  }
  return "llama-3.3-70b-versatile";
}

// ─── Provider URL map ───────────────────────────────────────────
function getProviderUrl(provider) {
  return {
    groq:     "https://api.groq.com/openai/v1/chat/completions",
    cerebras: "https://api.cerebras.ai/v1/chat/completions",
    mistral:  "https://api.mistral.ai/v1/chat/completions",
    gemini:   "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
  }[provider] ?? "https://api.groq.com/openai/v1/chat/completions";
}

// ─── Core AI call ───────────────────────────────────────────────
async function getAIResponse(modelName, messages, env) {
  const info = ALL_MODELS[modelName];
  if (!info) return "❌ מודל לא ידוע.";

  const provider = info.provider;
  const rawKeys = env[`${provider.toUpperCase()}_API_KEY`] ?? "";
  const apiKeys = rawKeys.split(",").map(k => k.trim()).filter(Boolean);
  if (!apiKeys.length) return `❌ חסר API KEY עבור ${provider}`;

  const apiId   = info.api_id ?? modelName;
  const url     = getProviderUrl(provider);
  const timeout = HEAVY_MODELS.has(modelName) ? 180_000 : 90_000;

  const systemMsg = { role: "system", content: "You are a helpful assistant. Respond in Hebrew. Be accurate and concise." };

  for (const key of apiKeys) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeout);
    try {
      const res = await fetch(url, {
        method: "POST",
        headers: { "Authorization": `Bearer ${key}`, "Content-Type": "application/json" },
        body: JSON.stringify({ model: apiId, messages: [systemMsg, ...messages] }),
        signal: controller.signal,
      });
      clearTimeout(timer);

      if (res.ok) {
        const data = await res.json();
        return data.choices[0].message.content;
      }
      if (res.status === 429) { blockModel(modelName); continue; }
      if (res.status === 404) return `❌ המודל ${modelName} לא נמצא (404).`;

      const errData = await res.json().catch(() => ({}));
      const errMsg  = errData?.error?.message ?? "שגיאה לא ידועה";
      return `❌ שגיאה ${res.status}: ${errMsg}`;

    } catch (e) {
      clearTimeout(timer);
      if (e.name === "AbortError") return "❌ timeout — המודל עמוס, נסה שוב.";
      // Network error — try next key
      continue;
    }
  }
  return "❌ כל המפתחות הגיעו ל-rate limit. נסה בעוד דקה.";
}

// ─── CORS helpers ───────────────────────────────────────────────
function corsHeaders(env, requestOrigin) {
  const allowed = env.ALLOWED_ORIGIN ?? "*";
  const origin  = allowed === "*" ? "*" : requestOrigin;
  return {
    "Access-Control-Allow-Origin":  origin ?? "*",
    "Access-Control-Allow-Headers": "Content-Type, X-Site-Password",
    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
  };
}

function jsonResponse(data, status, extraHeaders) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json", ...extraHeaders },
  });
}

// ─── Password check ─────────────────────────────────────────────
function checkPassword(request, env) {
  const pw = env.SITE_PASSWORD ?? "";
  if (!pw) return false;
  const incoming = request.headers.get("X-Site-Password") ?? "";
  // Constant-time compare
  if (incoming.length !== pw.length) return false;
  let diff = 0;
  for (let i = 0; i < pw.length; i++) diff |= pw.charCodeAt(i) ^ incoming.charCodeAt(i);
  return diff === 0;
}

// ─── Route handlers ─────────────────────────────────────────────
async function handleVerify(request, env, cors) {
  if (!checkPassword(request, env)) {
    return jsonResponse({ ok: false, error: "סיסמה שגויה" }, 401, cors);
  }
  return jsonResponse({ ok: true }, 200, cors);
}

async function handleChat(request, env, cors) {
  if (!checkPassword(request, env)) {
    return jsonResponse({ error: "לא מורשה — סיסמה שגויה" }, 401, cors);
  }

  let body;
  try { body = await request.json(); } catch { body = {}; }

  const messages   = body.messages ?? [];
  let   modelName  = body.model ?? "auto";

  if (!messages.length) {
    return jsonResponse({ error: "חסרות הודעות" }, 400, cors);
  }

  if (modelName === "auto") {
    const lastUser = [...messages].reverse().find(m => m.role === "user");
    modelName = autoSelectModel(lastUser?.content ?? "");
  }

  const reply = await getAIResponse(modelName, messages, env);
  return jsonResponse({
    reply,
    model_used: modelName,
    model_heb:  ALL_MODELS[modelName]?.heb ?? modelName,
  }, 200, cors);
}

function handleModels(env, cors) {
  const result = [];
  for (const [id, info] of Object.entries(ALL_MODELS)) {
    if (!info.category) continue; // skip "auto" system entry
    result.push({
      id,
      heb:      info.heb ?? id,
      provider: info.provider ?? "",
      blocked:  isBlocked(id),
    });
  }
  return jsonResponse(result, 200, cors);
}

function handleHealth(cors) {
  return jsonResponse({ status: "ok" }, 200, cors);
}

// ─── Main fetch handler ─────────────────────────────────────────
export default {
  async fetch(request, env) {
    const origin  = request.headers.get("Origin") ?? "";
    const cors    = corsHeaders(env, origin);
    const url     = new URL(request.url);
    const method  = request.method;

    // Preflight
    if (method === "OPTIONS") {
      return new Response(null, { status: 204, headers: cors });
    }

    // Route
    if (url.pathname === "/api/health") {
      return handleHealth(cors);
    }
    if (url.pathname === "/api/models" && method === "GET") {
      return handleModels(env, cors);
    }
    if (url.pathname === "/api/verify" && method === "POST") {
      return handleVerify(request, env, cors);
    }
    if (url.pathname === "/api/chat" && method === "POST") {
      return handleChat(request, env, cors);
    }

    return new Response("Not Found", { status: 404, headers: cors });
  },
};
