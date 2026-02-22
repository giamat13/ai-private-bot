"""
api.py — Flask backend for the AI Chat web interface.
Deploy this on Render / Railway / Fly.io (NOT GitHub Pages).

Required environment variables (set in your hosting dashboard, NEVER in code):
  SITE_PASSWORD    — the password users enter on the website
  GROQ_API_KEY     — Groq API key(s), comma-separated
  CEREBRAS_API_KEY — Cerebras API key(s)
  GEMINI_API_KEY   — Google Gemini API key(s)
  MISTRAL_API_KEY  — Mistral API key(s)
  TAVILY_API_KEY   — (optional) Tavily search key
  ALLOWED_ORIGIN   — your GitHub Pages URL, e.g. https://yourusername.github.io
"""

import os
import secrets
import json
import datetime
import requests
from flask import Flask, request, jsonify
from flask_cors import CORS
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)

# ─── CORS: only allow your GitHub Pages domain ──────────────────────────────
ALLOWED_ORIGIN = os.getenv("ALLOWED_ORIGIN", "*")   # set this in production!
CORS(app, origins=[ALLOWED_ORIGIN], supports_credentials=True)

# ─── Password check ──────────────────────────────────────────────────────────
SITE_PASSWORD = os.getenv("SITE_PASSWORD", "")

def check_password(req) -> bool:
    """
    The password is sent by the browser in the X-Site-Password header.
    We compare with secrets.compare_digest to prevent timing-attack leaks.
    The actual password is ONLY in the server's env var — never in any code or HTML.
    """
    if not SITE_PASSWORD:
        return False  # if env var not set, deny everything
    incoming = req.headers.get("X-Site-Password", "")
    return secrets.compare_digest(incoming, SITE_PASSWORD)

# ─── Model definitions (same as main.py) ────────────────────────────────────
ALL_MODELS = {
    "auto": {"provider": "system", "heb": "בחירה אוטומטית", "category": "auto"},
    # Groq
    "llama-3.1-8b-instant":    {"provider": "groq", "api_id": "llama-3.1-8b-instant",                         "heb": "Llama 3.1 8B",      "category": "groq"},
    "llama-3.3-70b-versatile": {"provider": "groq", "api_id": "llama-3.3-70b-versatile",                      "heb": "Llama 3.3 70B",     "category": "groq"},
    "llama-4-maverick":        {"provider": "groq", "api_id": "meta-llama/llama-4-maverick-17b-128e-instruct", "heb": "Llama 4 Maverick",  "category": "groq"},
    "llama-4-scout":           {"provider": "groq", "api_id": "meta-llama/llama-4-scout-17b-16e-instruct",     "heb": "Llama 4 Scout",     "category": "groq"},
    "kimi-k2":                 {"provider": "groq", "api_id": "moonshotai/kimi-k2-instruct",                   "heb": "Kimi K2",           "category": "groq"},
    "gpt-oss-120b":            {"provider": "groq", "api_id": "openai/gpt-oss-120b",                          "heb": "GPT OSS 120B",      "category": "groq"},
    "gpt-oss-20b":             {"provider": "groq", "api_id": "openai/gpt-oss-20b",                           "heb": "GPT OSS 20B",       "category": "groq"},
    "qwen3-32b":               {"provider": "groq", "api_id": "qwen/qwen3-32b",                               "heb": "Qwen 3 32B",        "category": "groq"},
    "groq-compound":           {"provider": "groq", "api_id": "groq/compound",                                "heb": "Groq Compound",     "category": "groq"},
    "groq-compound-mini":      {"provider": "groq", "api_id": "groq/compound-mini",                           "heb": "Groq Compound Mini","category": "groq"},
    # Cerebras
    "cerebras-llama3.1-8b":    {"provider": "cerebras", "api_id": "llama3.1-8b",       "heb": "Llama 3.1 8B (Cerebras)", "category": "cerebras"},
    "cerebras-gpt-oss-120b":   {"provider": "cerebras", "api_id": "gpt-oss-120b",      "heb": "GPT OSS 120B (Cerebras)", "category": "cerebras"},
    # Gemini
    "gemini-2.5-flash-lite":   {"provider": "gemini", "api_id": "gemini-2.5-flash-lite",     "heb": "Gemini 2.5 Flash-Lite", "category": "gemini"},
    "gemini-2.5-flash":        {"provider": "gemini", "api_id": "gemini-2.5-flash",          "heb": "Gemini 2.5 Flash",      "category": "gemini"},
    "gemini-2.5-pro":          {"provider": "gemini", "api_id": "gemini-2.5-pro",            "heb": "Gemini 2.5 Pro",        "category": "gemini"},
    # Mistral
    "mistral-large":           {"provider": "mistral", "api_id": "mistral-large-latest",  "heb": "Mistral Large",  "category": "mistral"},
    "mistral-medium":          {"provider": "mistral", "api_id": "mistral-medium-latest", "heb": "Mistral Medium", "category": "mistral"},
    "mistral-small":           {"provider": "mistral", "api_id": "mistral-small-latest",  "heb": "Mistral Small",  "category": "mistral"},
}

HEAVY_MODELS = {"kimi-k2", "gpt-oss-120b", "llama-4-maverick", "groq-compound",
                "llama-4-scout", "qwen3-32b", "cerebras-gpt-oss-120b",
                "mistral-large", "gemini-2.5-pro"}

PRIORITY_ORDER = [
    "groq-compound-mini", "kimi-k2", "qwen3-32b", "llama-4-maverick",
    "gpt-oss-120b", "gpt-oss-20b", "llama-3.3-70b-versatile",
    "llama-3.1-8b-instant", "cerebras-gpt-oss-120b", "cerebras-llama3.1-8b",
    "gemini-2.5-flash", "gemini-2.5-flash-lite", "mistral-large", "mistral-small",
]

# ─── In-memory blocked models (resets on server restart) ────────────────────
_blocked: dict = {}  # model_or_provider → unblock_timestamp

def is_blocked(model_name: str) -> bool:
    now = datetime.datetime.now().timestamp()
    # clean expired
    expired = [k for k, v in _blocked.items() if v != -1 and v <= now]
    for k in expired:
        del _blocked[k]
    if model_name in _blocked:
        return True
    provider = ALL_MODELS.get(model_name, {}).get("provider", "")
    return f"_provider_{provider}" in _blocked

def block_model(model_name: str, seconds: int = 60):
    provider = ALL_MODELS.get(model_name, {}).get("provider", "groq")
    provider_scope = {"mistral": True, "gemini": True}.get(provider, False)
    key = f"_provider_{provider}" if provider_scope else model_name
    _blocked[key] = datetime.datetime.now().timestamp() + seconds

# ─── AI call ────────────────────────────────────────────────────────────────
def get_ai_response(model_name: str, messages: list) -> str:
    info = ALL_MODELS.get(model_name)
    if not info:
        return "❌ מודל לא ידוע."

    provider = info["provider"]
    raw_keys = os.getenv(f"{provider.upper()}_API_KEY", "")
    api_keys = [k.strip() for k in raw_keys.split(",") if k.strip()]
    if not api_keys:
        return f"❌ חסר API KEY עבור {provider}"

    api_id = info.get("api_id", model_name)
    url_map = {
        "groq":     "https://api.groq.com/openai/v1/chat/completions",
        "cerebras": "https://api.cerebras.ai/v1/chat/completions",
        "mistral":  "https://api.mistral.ai/v1/chat/completions",
        "gemini":   "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
    }
    url = url_map.get(provider, url_map["groq"])
    timeout = 180 if model_name in HEAVY_MODELS else 90

    for key in api_keys:
        try:
            res = requests.post(
                url,
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                json={"model": api_id, "messages": messages},
                timeout=timeout
            )
            if res.status_code == 200:
                return res.json()["choices"][0]["message"]["content"]
            elif res.status_code == 429:
                block_model(model_name)
                continue
            elif res.status_code == 404:
                return f"❌ המודל {model_name} לא נמצא (404)."
            else:
                err = res.json().get("error", {}).get("message", "שגיאה לא ידועה")
                return f"❌ שגיאה {res.status_code}: {err}"
        except requests.Timeout:
            return "❌ timeout — המודל עמוס, נסה שוב."
        except Exception as e:
            return f"❌ שגיאת רשת: {e}"

    return "❌ כל המפתחות הגיעו ל-rate limit. נסה בעוד דקה."


def auto_select_model(user_msg: str) -> str:
    """Select best available model based on keywords."""
    text = user_msg.lower()
    kw_map = {
        "groq-compound-mini": ["מחיר", "היום", "עכשיו", "חדשות", "מזג אוויר", "weather", "news", "today", "price"],
        "kimi-k2": ["python", "javascript", "java", "html", "css", "sql", "קוד", "code", "bug", "debug", "תכנות"],
        "qwen3-32b": ["אינטגרל", "גזירה", "מטריצה", "חשבון", "math", "integral", "matrix", "הסתברות"],
    }
    for model_key, keywords in kw_map.items():
        if not is_blocked(model_key) and any(kw in text for kw in keywords):
            return model_key

    for fallback in PRIORITY_ORDER:
        if not is_blocked(fallback) and fallback in ALL_MODELS:
            return fallback
    return "llama-3.3-70b-versatile"


# ─── Routes ─────────────────────────────────────────────────────────────────

@app.route("/api/verify", methods=["POST"])
def verify():
    """Check if the password is correct — returns 200 or 401."""
    if check_password(request):
        return jsonify({"ok": True})
    return jsonify({"ok": False, "error": "סיסמה שגויה"}), 401


@app.route("/api/chat", methods=["POST"])
def chat():
    """Main chat endpoint."""
    if not check_password(request):
        return jsonify({"error": "לא מורשה — סיסמה שגויה"}), 401

    body = request.get_json(silent=True) or {}
    messages = body.get("messages", [])
    model_name = body.get("model", "auto")

    if not messages:
        return jsonify({"error": "חסרות הודעות"}), 400

    # Resolve "auto"
    if model_name == "auto":
        last_user = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
        model_name = auto_select_model(last_user)

    system_msg = {"role": "system", "content": "You are a helpful assistant. Respond in Hebrew. Be accurate and concise."}
    full_messages = [system_msg] + messages

    reply = get_ai_response(model_name, full_messages)
    return jsonify({"reply": reply, "model_used": model_name, "model_heb": ALL_MODELS.get(model_name, {}).get("heb", model_name)})


@app.route("/api/models", methods=["GET"])
def models():
    """Returns list of models (no auth needed — no secrets here)."""
    result = []
    for key, info in ALL_MODELS.items():
        if info.get("category") == "auto":
            continue
        result.append({"id": key, "heb": info.get("heb", key), "provider": info.get("provider", ""), "blocked": is_blocked(key)})
    return jsonify(result)


@app.route("/api/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
