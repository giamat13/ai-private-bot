import os
import json
import re
import requests
import datetime
import sys
import io
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, MessageHandler, CommandHandler, filters, ContextTypes

# תיקון בעיית קידוד בטרמינל
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

load_dotenv()
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
HISTORY_DIR = "history"
USERS_FILE = "allowed_users.txt"

# --- הגדרות מודלים ---
ALL_MODELS = {
    # ===== AUTO (קטגוריה נפרדת) =====
    "auto": {"provider": "system", "heb": "בחירה אוטומטית חכמה", "speed": "משתנה", "category": "auto"},

    # ===== Groq Cloud =====
    "llama-3.1-8b-instant":   {"provider": "groq", "api_id": "llama-3.1-8b-instant",                        "heb": "Llama 3.1 8B",       "speed": "מיידי",     "category": "groq"},
    "llama-3.3-70b-versatile":{"provider": "groq", "api_id": "llama-3.3-70b-versatile",                     "heb": "Llama 3.3 70B",      "speed": "מהיר מאוד","category": "groq"},
    "llama-4-maverick":       {"provider": "groq", "api_id": "meta-llama/llama-4-maverick-17b-12e-preview",  "heb": "Llama 4 Maverick",   "speed": "חדש",      "category": "groq"},
    "llama-4-scout":          {"provider": "groq", "api_id": "meta-llama/llama-4-scout-17b-16e-instruct",    "heb": "Llama 4 Scout",      "speed": "חכם",      "category": "groq"},
    "kimi-k2":                {"provider": "groq", "api_id": "moonshotai/kimi-k2-instruct",                  "heb": "Kimi K2",            "speed": "איכותי",   "category": "groq"},
    "gpt-oss-120b":           {"provider": "groq", "api_id": "openai/gpt-oss-120b",                         "heb": "GPT OSS 120B",       "speed": "עוצמתי",   "category": "groq"},
    "gpt-oss-20b":            {"provider": "groq", "api_id": "openai/gpt-oss-20b",                          "heb": "GPT OSS 20B",        "speed": "מהיר",     "category": "groq"},
    "qwen3-32b":              {"provider": "groq", "api_id": "qwen/qwen3-32b",                              "heb": "Qwen 3 32B",         "speed": "חכם מאוד", "category": "groq"},
    "groq-compound":          {"provider": "groq", "api_id": "groq/compound",                               "heb": "Groq Compound",      "speed": "משולב",    "category": "groq"},
    "groq-compound-mini":     {"provider": "groq", "api_id": "groq/compound-mini",                          "heb": "Groq Compound Mini", "speed": "מיידי",    "category": "groq"},
}

# ===== פרופיל מודלים לבחירה אוטומטית (מבוסס benchmarks אמיתיים) =====
MODEL_PROFILES = {
    "llama-3.1-8b-instant": {
        "best_for": "שיחות יומיומיות, שאלות פשוטות, סיכומים קצרים",
        "emoji": "⚡",
        "keywords": [],
        "description": "מהיר ביותר (166 t/s, TTFT 0.33s). מספיק לרוב השאלות הקצרות."
    },
    "llama-3.3-70b-versatile": {
        "best_for": "כתיבה, עברית, ניתוח טקסט, תרגום, שאלות כלליות מורכבות",
        "emoji": "✍️",
        "keywords": ["כתוב", "תרגם", "נתח", "הסבר", "תאר", "write", "translate", "explain",
                     "analyze", "describe", "summarize", "סכם"],
        "description": "מצוין בהוראות (IFEval 92.1). ביצועים שווים ל-Llama 3.1 405B."
    },
    "kimi-k2": {
        "best_for": "קוד, תכנות, debugging, הנדסת תוכנה, אוטומציה",
        "emoji": "💻",
        "keywords": ["קוד", "code", "python", "javascript", "typescript", "java", "c++", "rust", "go",
                     "bug", "פונקצי", "script", "תכנות", "program", "develop", "debug", "api",
                     "class", "function", "אלגוריתם", "html", "css", "sql", "git", "react", "node",
                     "bash", "shell", "docker", "kubernetes", "framework", "library", "endpoint"],
        "description": "מוביל open-source בקוד (SWE-bench 65.8%, LiveCodeBench 53.7). 1T פרמטרים, מצוין ב-tool use."
    },
    "qwen3-32b": {
        "best_for": "מתמטיקה, STEM, לוגיקה, חישובים, פיזיקה, כימיה",
        "emoji": "🔢",
        "keywords": ["חשב", "מתמטיק", "משוואה", "סטטיסטיק", "math", "calculate", "formula", "proof",
                     "physics", "chemistry", "biology", "science", "equation", "integral", "derivative",
                     "probability", "הוכח", "פיזיק", "כימי", "ביולוג", "לוגיק", "logic", "הסתברות",
                     "גזירה", "אינטגרל", "מטריצ", "וקטור", "trigonometry", "geometry", "algebra"],
        "description": "מוביל ב-MATH benchmark (83+). חזק במיוחד ב-STEM ו-reasoning מתמטי."
    },
    "gpt-oss-120b": {
        "best_for": "מחקר עמוק, reasoning מורכב, פילוסופיה, השוואות, ניתוח אסטרטגי",
        "emoji": "🧠",
        "keywords": ["מחקר", "research", "מורכב", "complex", "השווה", "compare", "evaluate", "critique",
                     "comprehensive", "elaborate", "in-depth", "לעומק", "ניתוח מעמיק", "פילוסופ",
                     "אסטרטגי", "strategic", "pros and cons", "יתרונות וחסרונות"],
        "description": "המודל הגדול ביותר (120B). לניתוחים שדורשים עומק ו-reasoning מרובה שלבים."
    }
}

# סדר עדיפויות לבדיקת keywords (מהספציפי לכללי)
PRIORITY_ORDER = ["kimi-k2", "qwen3-32b", "gpt-oss-120b", "llama-3.3-70b-versatile", "llama-3.1-8b-instant"]

# ברירת מחדל = auto
DEFAULT_MODEL = "auto"

if not os.path.exists(HISTORY_DIR):
    os.makedirs(HISTORY_DIR)

# --- פונקציות עזר ---

def get_ollama_models():
    try:
        res = requests.get("http://localhost:11434/api/tags", timeout=2)
        if res.status_code == 200:
            return [m['name'] for m in res.json().get('models', [])]
    except: return []
    return []

def is_authorized(user):
    if not user: return False
    if not os.path.exists(USERS_FILE):
        with open(USERS_FILE, 'w', encoding='utf-8') as f: f.write("Giamat13\n")
        return True
    with open(USERS_FILE, 'r', encoding='utf-8') as f:
        allowed = [line.strip() for line in f.readlines() if line.strip()]
    return (user.username in allowed or str(user.id) in allowed)

def search_tavily(query):
    api_key = os.getenv("TAVILY_API_KEY")
    if not api_key: return "שירות החיפוש לא מוגדר."
    url = "https://api.tavily.com/search"
    payload = {"api_key": api_key, "query": query, "search_depth": "basic", "max_results": 3}
    try:
        res = requests.post(url, json=payload, timeout=10)
        if res.status_code == 200:
            results = res.json().get("results", [])
            return "\n".join([f"מקור: {r['title']}\nתוכן: {r['content']}" for r in results])
    except: pass
    return "לא נמצא מידע עדכני."

def select_model_by_keywords(text: str) -> str:
    """
    שלב 1 — keywords ברורים וחד-משמעיים (קוד, מתמטיקה):
    אם יש התאמה ברורה → נתב מיד.

    שלב 2 — סיווג עם Llama 8B (מהיר, חינמי):
    שולח שאלת סיווג קצרה ומקבל תשובה מובנית.
    """
    text_lower = text.lower()

    # --- שלב 1: keywords חזקים וחד-משמעיים ---
    hard_code_kw = [
        "python", "javascript", "typescript", "java", "c++", "rust", "go", "kotlin", "swift",
        "html", "css", "sql", "bash", "shell", "dockerfile", "kubernetes", "react", "node",
        "קוד", "code", "bug", "debug", "script", "פונקצי", "function", "class", "api",
        "תכנות", "program", "develop", "git", "endpoint", "framework", "אלגוריתם", "compiler"
    ]
    hard_math_kw = [
        "integral", "derivative", "matrix", "eigenvalue", "differential", "theorem",
        "אינטגרל", "גזירה", "מטריצה", "משפט", "הוכחה", "proof",
        "trigonometry", "calculus", "algebra", "geometry", "statistics", "probability",
        "הסתברות", "סטטיסטיק", "חשבון דיפרנציאלי"
    ]

    if any(kw in text_lower for kw in hard_code_kw):
        return "kimi-k2"
    if any(kw in text_lower for kw in hard_math_kw):
        return "qwen3-32b"

    # --- שלב 2: סיווג חכם עם Llama 8B ---
    classify_prompt = f"""You are a routing assistant. Classify the user's question into ONE category.

Categories:
- SIMPLE: greetings, casual chat, simple yes/no questions, short definitions
- WRITING: writing tasks, summaries, translations, explanations, Hebrew text, editing
- MATH: math, calculations, numbers, formulas, science problems, physics, chemistry, biology, STEM
- CODE: programming, code, debugging, software, scripts, algorithms, databases
- RESEARCH: deep analysis, comparisons, strategy, philosophy, multi-step reasoning, academic topics

User question: "{text}"

Reply with ONLY one word from the list above."""

    result = get_ai_response_universal("llama-3.1-8b-instant", [{"role": "user", "content": classify_prompt}])
    category = result.strip().upper().split()[0] if result else "WRITING"

    routing = {
        "SIMPLE":   "llama-3.1-8b-instant",
        "WRITING":  "llama-3.3-70b-versatile",
        "MATH":     "qwen3-32b",
        "CODE":     "kimi-k2",
        "RESEARCH": "gpt-oss-120b",
    }
    return routing.get(category, "llama-3.3-70b-versatile")

def needs_internet_search(text: str) -> bool:
    """זיהוי שאלות שדורשות מידע עדכני מהאינטרנט."""
    internet_keywords = [
        "מחיר", "price", "היום", "today", "עכשיו", "now", "חדשות", "news",
        "מזג אוויר", "weather", "מניה", "stock", "שער", "rate", "exchange",
        "אירוע", "event", "מתי נפתח", "שעות פעילות", "opening hours",
        "עדכון", "update", "אחרון", "latest", "recent", "2024", "2025", "2026"
    ]
    text_lower = text.lower()
    return any(kw in text_lower for kw in internet_keywords)

# --- ליבת ה-AI ---

def get_ai_response_universal(model_name, messages):
    ollama_local = get_ollama_models()
    if model_name in ollama_local:
        try:
            res = requests.post("http://localhost:11434/api/chat",
                                json={"model": model_name, "messages": messages, "stream": False}, timeout=120)
            return res.json().get("message", {}).get("content", "שגיאה בתשובת אולמה")
        except Exception as e: return f"❌ אולמה לא זמין: {e}"

    info = ALL_MODELS.get(model_name)
    if not info: return "❌ מודל לא מוכר במערכת."

    provider = info["provider"]
    key_env_name = f"{provider.upper()}_API_KEY"
    raw_keys = os.getenv(key_env_name, "")
    api_keys = [k.strip() for k in raw_keys.split(",") if k.strip()]

    if not api_keys:
        return f"❌ חסר API KEY עבור {provider} (.env)"

    actual_api_id = info.get("api_id", model_name)

    for key in api_keys:
        try:
            url = "https://api.groq.com/openai/v1/chat/completions"
            headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
            payload = {
                "model": actual_api_id,
                "messages": [{"role": "system", "content": "You are a helpful assistant. Respond in Hebrew. Be accurate."}] + messages
            }
            res = requests.post(url, headers=headers, json=payload, timeout=60)

            if res.status_code == 200:
                return res.json()['choices'][0]['message']['content']
            else:
                error_detail = res.json().get('error', {}).get('message', 'Unknown error')
                print(f"Provider {provider} Error: {res.status_code} - {error_detail}")
        except Exception as e:
            print(f"Exception during {provider} request: {e}")
            continue

    return f"❌ תקלה בתקשורת עם {provider}. וודא שהטוקן תקין והמודל זמין ב-Groq."

# --- טיפול בהודעות ---

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_authorized(user): return
    user_text = update.message.text
    user_dir = os.path.join(HISTORY_DIR, str(user.id))
    os.makedirs(user_dir, exist_ok=True)

    # טעינת הגדרות משתמש — ברירת מחדל = auto
    model_name = DEFAULT_MODEL
    settings_path = os.path.join(user_dir, "settings.json")
    if os.path.exists(settings_path):
        with open(settings_path, 'r') as f:
            model_name = json.load(f).get('model', DEFAULT_MODEL)

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    current_model = model_name
    final_query = user_text

    if model_name == "auto":
        # ===== בחירה אוטומטית חכמה מבוססת benchmarks אמיתיים =====
        # ⚡ llama-3.1-8b-instant    → שאלות קצרות/יומיומיות     (166 t/s)
        # ✍️  llama-3.3-70b-versatile → כתיבה, עברית, ניתוח       (IFEval 92.1)
        # 💻 kimi-k2                 → קוד ותכנות                (SWE-bench 65.8%)
        # 🔢 qwen3-32b               → מתמטיקה ו-STEM             (MATH 83+)
        # 🧠 gpt-oss-120b            → מחקר ו-reasoning מורכב    (120B params)
        current_model = select_model_by_keywords(user_text)

        if needs_internet_search(user_text):
            search_data = search_tavily(user_text)
            final_query = f"מידע מהאינטרנט:\n{search_data}\n\nשאלה: {user_text}"

    # טעינת היסטוריה
    hist_path = os.path.join(user_dir, "history.json")
    history = []
    if os.path.exists(hist_path):
        with open(hist_path, 'r', encoding='utf-8') as f:
            try: history = json.load(f)
            except: history = []

    history.append({"role": "user", "content": user_text})

    ai_response = get_ai_response_universal(current_model, history[:-1] + [{"role": "user", "content": final_query}])

    history.append({"role": "assistant", "content": ai_response})

    with open(hist_path, 'w', encoding='utf-8') as f:
        json.dump(history[-20:], f, ensure_ascii=False, indent=2)

    # תגית מודל בסוף התשובה (רק במצב auto)
    if model_name == "auto":
        profile = MODEL_PROFILES.get(current_model, {})
        emoji = profile.get("emoji", "🤖")
        model_display = ALL_MODELS.get(current_model, {}).get("heb", current_model)
        ai_response += f"\n\n_{emoji} נענה ע\"י: {model_display}_"

    await update.message.reply_text(ai_response, parse_mode="Markdown")

async def change_model(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_authorized(user): return
    ollama_list = get_ollama_models()

    if not context.args:
        # ===== קטגוריה 1: AUTO =====
        msg = "━━━━━━━━━━━━━━━━━━━\n"
        msg += "🧠 *מצב AUTO — ברירת מחדל מומלצת*\n"
        msg += "━━━━━━━━━━━━━━━━━━━\n"
        msg += "`auto` — הבוט בוחר את המודל המתאים ביותר לכל שאלה\n"
        msg += "   📌 _מנתב לפי סוג: קוד / מתמטיקה / כתיבה / שיחה / מחקר_\n"
        msg += "   🌐 _מזהה אוטומטית מתי לחפש באינטרנט_\n"
        msg += "   💡 _כל תשובה מציינת איזה מודל ענה_\n\n"

        # ===== קטגוריה 2: Groq Cloud =====
        msg += "━━━━━━━━━━━━━━━━━━━\n"
        msg += "⚡ *מודלי Groq Cloud — בחירה ידנית*\n"
        msg += "━━━━━━━━━━━━━━━━━━━\n"
        groq_models = {k: v for k, v in ALL_MODELS.items() if v.get("category") == "groq"}
        for m, info in groq_models.items():
            profile = MODEL_PROFILES.get(m, {})
            best_for = profile.get("best_for", "")
            emoji = profile.get("emoji", "🔹")
            msg += f"{emoji} `{m}`\n   └ {info['heb']} ({info['speed']})"
            if best_for:
                msg += f"\n   📌 _{best_for}_"
            msg += "\n"

        # ===== קטגוריה 3: Ollama מקומי =====
        if ollama_list:
            msg += "\n━━━━━━━━━━━━━━━━━━━\n"
            msg += "🏠 *Local Ollama*\n"
            msg += "━━━━━━━━━━━━━━━━━━━\n"
            msg += "\n".join([f"🔹 `{m}`" for m in ollama_list]) + "\n"

        msg += "\n➡️ *שינוי מודל:* `/model <name>`"
        await update.message.reply_text(msg, parse_mode="Markdown")
        return

    new_model = context.args[0]
    if new_model in ALL_MODELS or new_model in ollama_list:
        user_dir = os.path.join(HISTORY_DIR, str(user.id))
        os.makedirs(user_dir, exist_ok=True)
        with open(os.path.join(user_dir, "settings.json"), 'w') as f:
            json.dump({"model": new_model}, f)

        if new_model == "auto":
            await update.message.reply_text(
                "✅ מצב *AUTO* הופעל 🧠\n_הבוט יבחר אוטומטית את המודל המתאים לכל שאלה_",
                parse_mode="Markdown"
            )
        else:
            profile = MODEL_PROFILES.get(new_model, {})
            best_for = profile.get("best_for", "")
            emoji = profile.get("emoji", "🤖")
            reply = f"✅ המודל הוחלף ל-`{new_model}` {emoji}"
            if best_for:
                reply += f"\n📌 _מצוין ל: {best_for}_"
            await update.message.reply_text(reply, parse_mode="Markdown")
    else:
        await update.message.reply_text("❌ המודל שציינת לא קיים ברשימה.")

if __name__ == '__main__':
    print("🚀 Bot starting — Auto mode active by default")
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("model", change_model))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.run_polling()