import os
import json
import re
import requests
import datetime
import sys
import io
import asyncio
import mimetypes

# אכיפת UTF-8 על Windows — חייב לפני כל דבר אחר
if sys.platform == "win32":
    os.environ.setdefault("PYTHONUTF8", "1")
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, MessageHandler, CommandHandler, CallbackQueryHandler, filters, ContextTypes, ConversationHandler

load_dotenv()
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
HISTORY_DIR = "history"
USERS_FILE = "allowed_users.txt"

# --- מצבי שיחה (ConversationHandler) ---
WAITING_NEWCHAT_NAME = 1
WAITING_DELCHAT_PICK = 2
WAITING_CHAT_PICK    = 3

# --- עזר לניהול צ'אטים ---

def get_user_dir(user_id: int) -> str:
    d = os.path.join(HISTORY_DIR, str(user_id))
    os.makedirs(d, exist_ok=True)
    return d

def settings_path(user_id: int) -> str:
    return os.path.join(get_user_dir(user_id), "settings.json")

def load_settings(user_id: int) -> dict:
    p = settings_path(user_id)
    if os.path.exists(p):
        try:
            with open(p, 'r', encoding='utf-8') as f: return json.load(f)
        except: pass
    return {"model": "auto", "active_chat": None}

def save_settings(user_id: int, data: dict):
    with open(settings_path(user_id), 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=True, indent=2)

# --- סטטיסטיקות שימוש ---

def stats_path(user_id: int) -> str:
    return os.path.join(get_user_dir(user_id), "stats.json")

def load_stats(user_id: int) -> dict:
    p = stats_path(user_id)
    if os.path.exists(p):
        try:
            with open(p, 'r', encoding='utf-8') as f: return json.load(f)
        except: pass
    return {"total_messages": 0, "models": {}, "first_use": None, "last_use": None}

def save_stats(user_id: int, data: dict):
    with open(stats_path(user_id), 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=True, indent=2)

def track_usage(user_id: int, model_name: str):
    """מתעד שימוש — קוראים לזה אחרי כל תשובה מוצלחת."""
    stats = load_stats(user_id)
    now   = datetime.datetime.now().strftime("%d/%m/%Y %H:%M")
    stats["total_messages"] += 1
    stats["models"][model_name] = stats["models"].get(model_name, 0) + 1
    if not stats["first_use"]:
        stats["first_use"] = now
    stats["last_use"] = now
    save_stats(user_id, stats)

def chat_path(user_id: int, chat_name: str) -> str:
    """מסלול קובץ ה-JSON של צ'אט לפי שם."""
    safe = "".join(c if c.isalnum() or c in "-_ " else "_" for c in chat_name).strip()
    return os.path.join(get_user_dir(user_id), f"chat_{safe}.json")

def list_chats(user_id: int) -> list[str]:
    """מחזיר רשימת שמות צ'אטים קיימים (לפי קבצי chat_*.json)."""
    d = get_user_dir(user_id)
    names = []
    for f in sorted(os.listdir(d)):
        if f.startswith("chat_") and f.endswith(".json"):
            name = f[5:-5].replace("_", " ")
            names.append(name)
    return names

def load_chat(user_id: int, chat_name: str) -> list:
    p = chat_path(user_id, chat_name)
    if os.path.exists(p):
        try:
            with open(p, 'r', encoding='utf-8') as f: return json.load(f)
        except: pass
    return []

def save_chat(user_id: int, chat_name: str, history: list):
    with open(chat_path(user_id, chat_name), 'w', encoding='utf-8') as f:
        json.dump(history[-20:], f, ensure_ascii=True, indent=2)

def delete_chat(user_id: int, chat_name: str) -> bool:
    p = chat_path(user_id, chat_name)
    if os.path.exists(p):
        os.remove(p)
        return True
    return False

def get_active_chat(user_id: int) -> str | None:
    return load_settings(user_id).get("active_chat")

def set_active_chat(user_id: int, chat_name: str | None):
    s = load_settings(user_id)
    s["active_chat"] = chat_name
    save_settings(user_id, s)

def chats_keyboard(user_id: int, callback_prefix: str, active: str = None) -> InlineKeyboardMarkup | None:
    """בונה מקלדת inline עם כל הצ'אטים הקיימים. מסמן את הפעיל וה-default."""
    chats = list_chats(user_id)
    if not chats: return None
    buttons = []
    for c in chats:
        label = "💬 "
        if c == DEFAULT_CHAT_NAME:
            label = "🏠 "
        if c == active:
            label += f"► {c}"
        else:
            label += c
        buttons.append([InlineKeyboardButton(label, callback_data=f"{callback_prefix}:{c}")])
    return InlineKeyboardMarkup(buttons)

DEFAULT_CHAT_NAME = "ראשי"

def ensure_default_chat(user_id: int):
    """מוודא שצ'אט ברירת מחדל 'ראשי' תמיד קיים, ומגדיר אותו כפעיל אם אין אחר."""
    chats = list_chats(user_id)
    if DEFAULT_CHAT_NAME not in chats:
        save_chat(user_id, DEFAULT_CHAT_NAME, [])
    active = get_active_chat(user_id)
    if not active:
        set_active_chat(user_id, DEFAULT_CHAT_NAME)

# --- הגדרות מודלים ---
ALL_MODELS = {
    # ===== AUTO (קטגוריה נפרדת) =====
    "auto": {"provider": "system", "heb": "בחירה אוטומטית חכמה", "speed": "משתנה", "category": "auto"},

    # ===== Groq Cloud =====
    "llama-3.1-8b-instant":   {"provider": "groq", "api_id": "llama-3.1-8b-instant",                        "heb": "Llama 3.1 8B",       "speed": "מיידי",     "category": "groq"},
    "llama-3.3-70b-versatile":{"provider": "groq", "api_id": "llama-3.3-70b-versatile",                     "heb": "Llama 3.3 70B",      "speed": "מהיר מאוד","category": "groq"},
    "llama-4-maverick":       {"provider": "groq", "api_id": "meta-llama/llama-4-maverick-17b-128e-instruct",  "heb": "Llama 4 Maverick",   "speed": "חדש",      "category": "groq"},
    "llama-4-scout":          {"provider": "groq", "api_id": "meta-llama/llama-4-scout-17b-16e-instruct",      "heb": "Llama 4 Scout",      "speed": "חכם",      "category": "groq"},
    "kimi-k2":                {"provider": "groq", "api_id": "moonshotai/kimi-k2-instruct",                  "heb": "Kimi K2",            "speed": "איכותי",   "category": "groq"},
    "gpt-oss-120b":           {"provider": "groq", "api_id": "openai/gpt-oss-120b",                         "heb": "GPT OSS 120B",       "speed": "עוצמתי",   "category": "groq"},
    "gpt-oss-20b":            {"provider": "groq", "api_id": "openai/gpt-oss-20b",                          "heb": "GPT OSS 20B",        "speed": "מהיר",     "category": "groq"},
    "qwen3-32b":              {"provider": "groq", "api_id": "qwen/qwen3-32b",                              "heb": "Qwen 3 32B",         "speed": "חכם מאוד", "category": "groq"},
    "groq-compound":          {"provider": "groq", "api_id": "groq/compound",                               "heb": "Groq Compound",      "speed": "משולב",    "category": "groq"},
    "groq-compound-mini":     {"provider": "groq", "api_id": "groq/compound-mini",                          "heb": "Groq Compound Mini", "speed": "מיידי",    "category": "groq"},
}

# ===== פרופיל מודלים לבחירה אוטומטית =====
MODEL_PROFILES = {
    "llama-3.1-8b-instant": {
        "best_for": "שיחות יומיומיות, שאלות פשוטות, סיכומים קצרים",
        "emoji": "⚡",
        "description": "מהיר ביותר (166 t/s). מספיק לרוב השאלות הקצרות."
    },
    "llama-3.3-70b-versatile": {
        "best_for": "כתיבה, עברית, ניתוח טקסט, תרגום, שאלות כלליות",
        "emoji": "✍️",
        "description": "מצוין בהוראות (IFEval 92.1). ביצועים שווים ל-Llama 3.1 405B."
    },
    "llama-4-scout": {
        "best_for": "מסמכים ארוכים מאוד, ניתוח קוד בסקייל, עיבוד קבצים ענקיים",
        "emoji": "📄",
        "description": "קונטקסט 10M טוקן — הכי ארוך בעולם. MoE 109B, מהיר על GPU אחד."
    },
    "llama-4-maverick": {
        "best_for": "רב-לשוניות, הנמקה מתקדמת, שאלות בשפות זרות, ידע כללי רחב",
        "emoji": "🌍",
        "description": "MMLU Pro 80.5%, GPQA 69.8%. עולה על GPT-4o ו-Gemini 2.0 Flash."
    },
    "kimi-k2": {
        "best_for": "קוד, תכנות, debugging, הנדסת תוכנה, אוטומציה",
        "emoji": "💻",
        "description": "מוביל open-source בקוד (SWE-bench 65.8%). 1T פרמטרים."
    },
    "qwen3-32b": {
        "best_for": "מתמטיקה, STEM, לוגיקה, חישובים, פיזיקה, כימיה",
        "emoji": "🔢",
        "description": "מוביל ב-MATH benchmark (83+). חזק ב-STEM ו-reasoning מתמטי."
    },
    "gpt-oss-20b": {
        "best_for": "שאלות בינוניות, תגובות מהירות ומדויקות",
        "emoji": "🎯",
        "description": "גרסה קלה ומהירה של GPT-OSS. איזון מצוין בין מהירות לאיכות."
    },
    "gpt-oss-120b": {
        "best_for": "מחקר עמוק, reasoning מורכב, פילוסופיה, השוואות, ניתוח אסטרטגי",
        "emoji": "🧠",
        "description": "המודל הגדול ביותר (120B). לניתוחים שדורשים עומק מרובה שלבים."
    },
    "groq-compound-mini": {
        "best_for": "שאלות על אירועים עדכניים, מחירים, חדשות, מזג אוויר",
        "emoji": "🔍",
        "description": "מחפש באינטרנט בעצמו. מהיר, שאלה אחת. עולה על GPT-4o-search."
    },
    "groq-compound": {
        "best_for": "מחקר מרובה מקורות, ניתוח עם ריצת קוד, שאלות שדורשות כמה חיפושים",
        "emoji": "🔬",
        "description": "עד 10 חיפושי אינטרנט + ריצת קוד בענן."
    },
}

# מודלים כבדים שדורשים timeout ארוך
HEAVY_MODELS = {"kimi-k2", "gpt-oss-120b", "llama-4-maverick", "groq-compound", "llama-4-scout", "qwen3-32b"}

PRIORITY_ORDER = [
    "groq-compound",
    "groq-compound-mini",
    "kimi-k2",
    "qwen3-32b",
    "llama-4-maverick",
    "llama-4-scout",
    "gpt-oss-120b",
    "gpt-oss-20b",
    "llama-3.3-70b-versatile",
    "llama-3.1-8b-instant",
]

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
    text_lower = text.lower()

    kw_map = {
        "groq-compound-mini": [
            "מחיר", "price", "היום", "today", "עכשיו", "now", "חדשות", "news",
            "מזג אוויר", "weather", "מניה", "stock", "שער חליפין", "exchange rate",
            "אירוע", "event", "שעות פתיחה", "opening hours", "latest", "recent",
        ],
        "groq-compound": [
            "חפש באינטרנט", "search the web", "מחקר עדכני", "recent research",
            "השווה בין מוצרים", "compare products", "מה קורה עם", "what happened with",
            "חדשות על", "news about", "עדכון על", "update on",
        ],
        "kimi-k2": [
            "python", "javascript", "typescript", "java", "c++", "rust", "go", "kotlin", "swift",
            "html", "css", "sql", "bash", "shell", "dockerfile", "kubernetes", "react", "node",
            "קוד", "code", "bug", "debug", "script", "פונקצי", "function", "class", "api",
            "תכנות", "program", "develop", "git", "endpoint", "framework", "אלגוריתם", "compiler",
        ],
        "qwen3-32b": [
            "integral", "derivative", "matrix", "eigenvalue", "differential", "theorem",
            "אינטגרל", "גזירה", "מטריצה", "משפט", "הוכחה", "proof",
            "trigonometry", "calculus", "algebra", "geometry", "statistics", "probability",
            "הסתברות", "סטטיסטיק", "חשבון דיפרנציאלי", "חשב ", "חשבי ", "כמה עולה",
        ],
        "llama-4-scout": [
            "מסמך ארוך", "long document", "קובץ ענק", "huge file", "כל הקוד", "entire codebase",
            "עשרות עמודים", "hundreds of pages", "ספר שלם", "full book",
        ],
    }
    for model_name, keywords in kw_map.items():
        if any(kw in text_lower for kw in keywords):
            return model_name

    ollama_models = get_ollama_models()
    ollama_section = ""
    if ollama_models:
        ollama_list_str = ", ".join(ollama_models)
        ollama_section = f"""
LOCAL: question that can be answered by a local model (available: {ollama_list_str}) — prefer local for privacy, speed, simple/medium tasks"""

    classify_prompt = f"""You are a routing assistant. Classify this question into ONE category.

INTERNET: needs current info — news, prices, weather, recent events, today's date facts
LONGDOC: analyzing very long documents, entire codebases, books (10M+ token context needed)
CODE: programming, debugging, software engineering, scripts, databases, algorithms
MATH: math, calculations, formulas, STEM, physics, chemistry, biology, statistics
MULTILINGUAL: question in non-Hebrew/English language, OR about language/translation tasks
RESEARCH: deep multi-step analysis, philosophy, strategy, academic, complex comparisons
WRITING: writing, editing, summarizing, explaining, Hebrew text tasks
SIMPLE: greeting, casual chat, simple yes/no, short factual question{ollama_section}

Question: "{text}"

Reply with ONLY one word."""

    result = get_ai_response_universal("llama-3.1-8b-instant", [{"role": "user", "content": classify_prompt}])
    category = result.strip().upper().split()[0] if result else "WRITING"

    if category == "LOCAL" and ollama_models:
        return ollama_models[0]

    routing = {
        "INTERNET":     "groq-compound-mini",
        "LONGDOC":      "llama-4-scout",
        "CODE":         "kimi-k2",
        "MATH":         "qwen3-32b",
        "MULTILINGUAL": "llama-4-maverick",
        "RESEARCH":     "gpt-oss-120b",
        "WRITING":      "llama-3.3-70b-versatile",
        "SIMPLE":       "llama-3.1-8b-instant",
    }
    return routing.get(category, "llama-3.3-70b-versatile")


# --- ליבת ה-AI ---

def get_ai_response_universal(model_name, messages, user_id: int = None):
    ollama_local = get_ollama_models()
    if model_name in ollama_local:
        try:
            res = requests.post(
                "http://localhost:11434/api/chat",
                json={"model": model_name, "messages": messages, "stream": False},
                timeout=180
            )
            return res.json().get("message", {}).get("content", "שגיאה בתשובת אולמה")
        except requests.exceptions.Timeout:
            return "❌ timeout — Ollama לקח יותר מדי זמן. נסה שוב."
        except Exception as e:
            return f"❌ אולמה לא זמין: {e}"

    info = ALL_MODELS.get(model_name)
    if not info: return "❌ מודל לא מוכר במערכת."

    provider = info["provider"]
    key_env_name = f"{provider.upper()}_API_KEY"
    raw_keys = os.getenv(key_env_name, "")
    api_keys = [k.strip() for k in raw_keys.split(",") if k.strip()]

    if not api_keys:
        return f"❌ חסר API KEY עבור {provider} (.env)"

    actual_api_id = info.get("api_id", model_name)

    # בנה system prompt — כולל זיכרון ו-tone אם קיימים
    base_system = "You are a helpful assistant. Respond in Hebrew. Be accurate."
    if user_id:
        mem_prompt = memory_system_prompt(user_id)
        if mem_prompt:
            base_system = base_system + "\n\n" + mem_prompt
        tone_prompt = tone_system_prompt(user_id)
        if tone_prompt:
            base_system = base_system + "\n\n" + tone_prompt

    # timeout לפי גודל מודל
    timeout_sec = 180 if model_name in HEAVY_MODELS else 90

    last_error = ""
    for key in api_keys:
        try:
            url = "https://api.groq.com/openai/v1/chat/completions"
            headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
            payload = {
                "model": actual_api_id,
                "messages": [
                    {"role": "system", "content": base_system}
                ] + messages
            }
            res = requests.post(url, headers=headers, json=payload, timeout=timeout_sec)

            if res.status_code == 200:
                return res.json()['choices'][0]['message']['content']
            elif res.status_code == 429:
                last_error = "הגעת למגבלת קצב (rate limit). נסה שוב בעוד רגע."
                print(f"Rate limit hit on key ...{key[-4:]}")
                continue
            elif res.status_code in (408, 504):
                last_error = "timeout מהשרת — המודל עמוס. נסה שוב."
                continue
            else:
                error_detail = res.json().get('error', {}).get('message', 'Unknown error')
                last_error = f"שגיאה {res.status_code}: {error_detail}"
                print(f"Provider {provider} Error: {res.status_code} - {error_detail}")

        except requests.exceptions.Timeout:
            last_error = (
                f"⏱️ timeout לאחר {timeout_sec} שניות.\n"
                f"המודל '{model_name}' לקח יותר מדי זמן.\n"
                f"נסה שוב, או עבור למודל מהיר יותר עם /model"
            )
            print(f"Timeout on model {model_name} after {timeout_sec}s")
            continue
        except Exception as e:
            last_error = str(e)
            print(f"Exception during {provider} request: {e}")
            continue

    return f"❌ {last_error or f'תקלה בתקשורת עם {provider}. וודא שהטוקן תקין.'}"


# --- Typing indicator מתמשך ברקע ---

async def _keep_typing(bot, chat_id: int):
    """שולח typing indicator כל 4 שניות כל עוד מחכים לתשובה."""
    try:
        while True:
            await bot.send_chat_action(chat_id=chat_id, action="typing")
            await asyncio.sleep(4)
    except asyncio.CancelledError:
        pass


# --- שליחת הודעה ארוכה בחלקים ---

async def send_long_message(update: Update, text: str, parse_mode: str = "Markdown"):
    """שולח הודעה. אם ארוכה מ-4000 תווים — מפצל לחלקים חכמים."""
    MAX_LEN = 4000

    if len(text) <= MAX_LEN:
        try:
            await update.message.reply_text(text, parse_mode=parse_mode)
        except Exception:
            await update.message.reply_text(text)
        return

    # פיצול לפי שורות
    chunks = []
    current = ""
    for line in text.split("\n"):
        if len(current) + len(line) + 1 > MAX_LEN:
            if current:
                chunks.append(current.strip())
            current = line
        else:
            current = current + "\n" + line if current else line
    if current.strip():
        chunks.append(current.strip())

    for i, chunk in enumerate(chunks):
        header = f"_חלק {i+1}/{len(chunks)}_\n\n" if len(chunks) > 1 else ""
        try:
            await update.message.reply_text(header + chunk, parse_mode=parse_mode)
        except Exception:
            await update.message.reply_text(header + chunk)


# --- ניתוח תשובת AI לאיתור קבצים/תמונות ---

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}
FILE_TAG_RE  = re.compile(r'\[FILE:\s*(https?://\S+?)(?:\s*\|\s*([^\]]+))?\]', re.IGNORECASE)
IMAGE_TAG_RE = re.compile(r'\[IMAGE:\s*(https?://\S+?)\]', re.IGNORECASE)
IMAGE_B64_RE = re.compile(r'\[IMAGE_B64:\s*(data:image/[a-z]+;base64,[A-Za-z0-9+/=]+)\]', re.IGNORECASE)

def parse_ai_response(text: str) -> dict:
    images    = [(m.group(1)) for m in IMAGE_TAG_RE.finditer(text)]
    files     = [(m.group(1), m.group(2) or os.path.basename(m.group(1))) for m in FILE_TAG_RE.finditer(text)]
    images_b64 = [(m.group(1)) for m in IMAGE_B64_RE.finditer(text)]

    url_re = re.compile(r'https?://\S+\.(?:jpg|jpeg|png|gif|webp|bmp)(?:\?\S*)?', re.IGNORECASE)
    for url in url_re.findall(text):
        if url not in images:
            images.append(url)

    clean = text
    clean = IMAGE_TAG_RE.sub('', clean)
    clean = FILE_TAG_RE.sub('', clean)
    clean = IMAGE_B64_RE.sub('', clean)
    clean = url_re.sub('', clean)
    clean = clean.strip()

    return {"text": clean, "images": images, "files": files, "images_b64": images_b64}


async def send_response_with_media(update: Update, context: ContextTypes.DEFAULT_TYPE, ai_response: str):
    parsed = parse_ai_response(ai_response)
    text   = parsed["text"]

    if text:
        await send_long_message(update, text)

    for url in parsed["images"]:
        try:
            await update.message.reply_photo(photo=url)
        except Exception as e:
            await update.message.reply_text(f"⚠️ לא ניתן לשלוח תמונה מ-URL:\n{url}\n({e})")

    for data_uri in parsed["images_b64"]:
        try:
            header, b64data = data_uri.split(",", 1)
            import base64
            img_bytes = base64.b64decode(b64data)
            ext = header.split("/")[1].split(";")[0]
            buf = io.BytesIO(img_bytes)
            buf.name = f"image.{ext}"
            await update.message.reply_photo(photo=buf)
        except Exception as e:
            await update.message.reply_text(f"⚠️ לא ניתן לשלוח תמונה (base64): {e}")

    for url, name in parsed["files"]:
        try:
            resp = requests.get(url, timeout=30)
            resp.raise_for_status()
            buf = io.BytesIO(resp.content)
            buf.name = name
            mime, _ = mimetypes.guess_type(name)
            if mime and mime.startswith("image/"):
                await update.message.reply_photo(photo=buf, filename=name)
            else:
                await update.message.reply_document(document=buf, filename=name)
        except Exception as e:
            await update.message.reply_text(f"⚠️ לא ניתן לשלוח קובץ '{name}':\n{url}\n({e})")


# --- טיפול בהודעות ---

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_authorized(user): return

    if context.user_data.get("waiting_for"):
        await handle_waiting_input(update, context)
        return

    user_text = update.message.text
    settings = load_settings(user.id)
    model_name = settings.get("model", DEFAULT_MODEL)

    ensure_default_chat(user.id)
    active_chat = get_active_chat(user.id)

    chat_id = update.effective_chat.id

    typing_task = asyncio.create_task(_keep_typing(context.bot, chat_id))

    try:
        current_model = model_name
        if model_name == "auto":
            current_model = select_model_by_keywords(user_text)

        history = load_chat(user.id, active_chat)

        _push_undo(context, history)

        history.append({"role": "user", "content": user_text})

        ai_response = get_ai_response_universal(current_model, history, user_id=user.id)

        history.append({"role": "assistant", "content": ai_response})
        save_chat(user.id, active_chat, history)
        track_usage(user.id, current_model)

        if model_name == "auto":
            profile = MODEL_PROFILES.get(current_model, {})
            emoji = profile.get("emoji", "🤖")
            model_display = ALL_MODELS.get(current_model, {}).get("heb", current_model)
            ai_response += f"\n\n_{emoji} נענה ע\"י: {model_display}_"

    finally:
        typing_task.cancel()
        try:
            await typing_task
        except asyncio.CancelledError:
            pass

    await send_response_with_media(update, context, ai_response)


async def handle_waiting_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    waiting = context.user_data.pop("waiting_for")
    text = update.message.text.strip()

    if waiting == "newchat_name":
        await _do_newchat(update, context, text)
    elif waiting == "delchat_name":
        await _do_delchat(update, context, text)
    elif waiting == "chat_name":
        await _do_switch_chat(update, context, text)
    elif waiting == "export_chat_name":
        await _do_export(update, context, text)
    elif waiting == "remember_fact":
        await _do_remember(update, context, text)
    elif waiting == "tone_custom":
        name = text[:30] + ("..." if len(text) > 30 else "")
        await _apply_tone(update, update.effective_user.id, name, text)


# ─────────────────────────────────────────────
#  קבלת תמונות / קבצים מהמשתמש
# ─────────────────────────────────────────────

IMAGE_MIME_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp", "image/bmp"}

async def download_telegram_file(context: ContextTypes.DEFAULT_TYPE, file_id: str) -> bytes:
    tg_file = await context.bot.get_file(file_id)
    buf = io.BytesIO()
    await tg_file.download_to_memory(buf)
    buf.seek(0)
    return buf.read()

def bytes_to_base64_uri(data: bytes, mime: str) -> str:
    import base64
    return f"data:{mime};base64,{base64.b64encode(data).decode()}"

def build_media_user_message(caption: str, media_type: str, filename: str,
                              base64_uri: str | None = None) -> list | str:
    text_part = caption.strip() if caption else "תאר/י את הקובץ הזה."

    if base64_uri:
        return [
            {"type": "text",      "text": text_part},
            {"type": "image_url", "image_url": {"url": base64_uri}},
        ]
    else:
        return f"[המשתמש שלח קובץ: {filename}]\n{text_part}"


async def handle_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_authorized(user): return

    msg = update.message
    caption = msg.caption or ""

    file_id   = None
    filename  = "קובץ"
    mime_type = "application/octet-stream"
    is_image  = False

    if msg.photo:
        file_id  = msg.photo[-1].file_id
        filename = "image.jpg"
        mime_type = "image/jpeg"
        is_image  = True
    elif msg.document:
        file_id   = msg.document.file_id
        filename  = msg.document.file_name or "document"
        mime_type = msg.document.mime_type or "application/octet-stream"
        is_image  = mime_type in IMAGE_MIME_TYPES
    elif msg.audio:
        file_id  = msg.audio.file_id
        filename = msg.audio.file_name or "audio.mp3"
        mime_type = msg.audio.mime_type or "audio/mpeg"
    elif msg.voice:
        file_id  = msg.voice.file_id
        filename = "voice.ogg"
        mime_type = "audio/ogg"
    elif msg.video:
        file_id  = msg.video.file_id
        filename = msg.video.file_name or "video.mp4"
        mime_type = msg.video.mime_type or "video/mp4"
    elif msg.video_note:
        file_id  = msg.video_note.file_id
        filename = "video_note.mp4"
        mime_type = "video/mp4"
    elif msg.sticker:
        file_id  = msg.sticker.file_id
        filename = "sticker.webp"
        mime_type = "image/webp"
        is_image  = True

    if not file_id:
        await msg.reply_text("⚠️ סוג קובץ זה אינו נתמך.")
        return

    settings    = load_settings(user.id)
    model_name  = settings.get("model", DEFAULT_MODEL)
    ensure_default_chat(user.id)
    active_chat = get_active_chat(user.id)
    chat_id     = update.effective_chat.id

    typing_task = asyncio.create_task(_keep_typing(context.bot, chat_id))

    try:
        current_model = model_name
        if model_name == "auto":
            current_model = "llama-4-maverick" if is_image else select_model_by_keywords(caption or filename)

        file_data = await download_telegram_file(context, file_id)

        base64_uri = None
        if is_image:
            base64_uri = bytes_to_base64_uri(file_data, mime_type)

        user_content = build_media_user_message(caption, mime_type, filename, base64_uri)

        history = load_chat(user.id, active_chat)

        _push_undo(context, history)

        history_entry = caption if caption else f"[שלח {filename}]"
        history.append({"role": "user", "content": history_entry})

        messages_for_ai = load_chat(user.id, active_chat)[:-1]
        messages_for_ai.append({"role": "user", "content": user_content})

        ai_response = get_ai_response_universal(current_model, messages_for_ai, user_id=user.id)

        history.append({"role": "assistant", "content": ai_response})
        save_chat(user.id, active_chat, history)

        if model_name == "auto":
            profile = MODEL_PROFILES.get(current_model, {})
            emoji = profile.get("emoji", "🤖")
            model_display = ALL_MODELS.get(current_model, {}).get("heb", current_model)
            ai_response += f"\n\n_{emoji} נענה ע\"י: {model_display}_"

    except Exception as e:
        ai_response = f"❌ שגיאה בעיבוד הקובץ: {e}"

    finally:
        typing_task.cancel()
        try:
            await typing_task
        except asyncio.CancelledError:
            pass

    await send_response_with_media(update, context, ai_response)


# --- /model ---

async def change_model(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_authorized(user): return
    ollama_list = get_ollama_models()

    if not context.args:
        msg = "━━━━━━━━━━━━━━━━━━━\n"
        msg += "🧠 *מצב AUTO — ברירת מחדל מומלצת*\n"
        msg += "━━━━━━━━━━━━━━━━━━━\n"
        ollama_note = ""
        if ollama_list:
            ollama_note = f"\n   🏠 _כולל מודלים מקומיים: {', '.join(ollama_list)}_"
        msg += "`auto` — הבוט בוחר את המודל המתאים ביותר לכל שאלה\n"
        msg += "   📌 _מנתב לפי סוג: קוד / מתמטיקה / כתיבה / שיחה / מחקר / מידע עדכני_\n"
        msg += "   🌐 _מזהה אוטומטית מתי נדרש חיפוש אינטרנט_\n"
        msg += f"   💡 _כל תשובה מציינת איזה מודל ענה_{ollama_note}\n\n"

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

        if ollama_list:
            msg += "\n━━━━━━━━━━━━━━━━━━━\n"
            msg += "🏠 *Local Ollama*\n"
            msg += "━━━━━━━━━━━━━━━━━━━\n"
            msg += "\n".join([f"🔹 `{m}`" for m in ollama_list]) + "\n"

        msg += "\n➡️ *שינוי מודל:* `/model <שם>`"
        await update.message.reply_text(msg, parse_mode="Markdown")
        return

    new_model = context.args[0]
    if new_model in ALL_MODELS or new_model in ollama_list:
        user_dir = os.path.join(HISTORY_DIR, str(user.id))
        os.makedirs(user_dir, exist_ok=True)
        with open(os.path.join(user_dir, "settings.json"), 'w', encoding='utf-8') as f:
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


# ─────────────────────────────────────────────
#  /newchat
# ─────────────────────────────────────────────

async def cmd_newchat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_authorized(user): return
    if context.args:
        await _do_newchat(update, context, " ".join(context.args))
    else:
        context.user_data["waiting_for"] = "newchat_name"
        await update.message.reply_text("💬 *שם לצ'אט החדש?*\nכתוב את השם:", parse_mode="Markdown")

async def _do_newchat(update: Update, context, name: str):
    user = update.effective_user
    name = name.strip()
    if not name:
        await update.message.reply_text("❌ שם לא יכול להיות ריק.")
        return
    save_chat(user.id, name, [])
    set_active_chat(user.id, name)
    await update.message.reply_text(f"✅ צ'אט '{name}' נוצר ופעיל! 🆕")


# ─────────────────────────────────────────────
#  /chat
# ─────────────────────────────────────────────

async def cmd_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_authorized(user): return
    if context.args:
        await _do_switch_chat(update, context, " ".join(context.args))
        return

    ensure_default_chat(user.id)
    chats = list_chats(user.id)
    active = get_active_chat(user.id)

    keyboard = chats_keyboard(user.id, "switch_chat", active=active)
    active_line = f"\nפעיל כרגע: {active}" if active else ""
    await update.message.reply_text(
        f"💬 בחר צ'אט:{active_line}\n\nאו כתוב את שם הצ'אט:",
        reply_markup=keyboard
    )
    context.user_data["waiting_for"] = "chat_name"

async def _do_switch_chat(update: Update, context, name: str):
    user = update.effective_user
    name = name.strip()
    ensure_default_chat(user.id)
    chats = list_chats(user.id)
    match = next((c for c in chats if c.lower() == name.lower()), None)
    if not match:
        await update.message.reply_text(f"❌ צ'אט '{name}' לא נמצא. השתמש ב /chat לרשימה.")
        return
    set_active_chat(user.id, match)
    history = load_chat(user.id, match)
    msgs = len([m for m in history if m["role"] == "user"])
    await update.message.reply_text(f"✅ עברת לצ'אט '{match}' 💬\n({msgs} הודעות קודמות)")

async def callback_switch_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    name = query.data.split(":", 1)[1]
    context.user_data.pop("waiting_for", None)
    user = query.from_user
    set_active_chat(user.id, name)
    history = load_chat(user.id, name)
    msgs = len([m for m in history if m["role"] == "user"])
    await query.edit_message_text(f"✅ עברת לצ'אט '{name}' 💬\n({msgs} הודעות קודמות)")


# ─────────────────────────────────────────────
#  /delchat
# ─────────────────────────────────────────────

async def cmd_delchat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_authorized(user): return
    if context.args:
        await _do_delchat(update, context, " ".join(context.args))
        return

    ensure_default_chat(user.id)
    chats = list_chats(user.id)
    if not chats:
        await update.message.reply_text("ℹ️ _אין צ'אטים למחוק_", parse_mode="Markdown")
        return

    keyboard = chats_keyboard(user.id, "del_chat", active=get_active_chat(user.id))
    await update.message.reply_text(
        "🗑️ *איזה צ'אט למחוק?*\n\nאו כתוב את שמו:",
        parse_mode="Markdown",
        reply_markup=keyboard
    )
    context.user_data["waiting_for"] = "delchat_name"

def confirm_keyboard(yes_data: str, no_data: str = "confirm:no") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ כן, מחק", callback_data=yes_data),
        InlineKeyboardButton("❌ לא",      callback_data=no_data),
    ]])


async def _do_delchat(update: Update, context, name: str):
    user = update.effective_user
    name = name.strip()
    chats = list_chats(user.id)
    match = next((c for c in chats if c.lower() == name.lower()), None)
    if not match:
        await update.message.reply_text(f"❌ צ'אט '{name}' לא נמצא.")
        return
    if match == DEFAULT_CHAT_NAME:
        await update.message.reply_text(
            f"⚠️ האם לנקות את היסטוריית הצ'אט '{DEFAULT_CHAT_NAME}'?",
            reply_markup=confirm_keyboard(f"confirm_delchat:{match}")
        )
    else:
        await update.message.reply_text(
            f"⚠️ האם למחוק את הצ'אט *'{match}'*?",
            parse_mode="Markdown",
            reply_markup=confirm_keyboard(f"confirm_delchat:{match}")
        )


async def callback_del_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    name = query.data.split(":", 1)[1]
    context.user_data.pop("waiting_for", None)

    if name == DEFAULT_CHAT_NAME:
        await query.edit_message_text(
            f"⚠️ האם לנקות את היסטוריית הצ'אט '{DEFAULT_CHAT_NAME}'?",
            reply_markup=confirm_keyboard(f"confirm_delchat:{name}")
        )
    else:
        await query.edit_message_text(
            f"⚠️ האם למחוק את הצ'אט *'{name}'*?",
            parse_mode="Markdown",
            reply_markup=confirm_keyboard(f"confirm_delchat:{name}")
        )


async def callback_confirm_delchat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    name = query.data.split(":", 1)[1]
    user = query.from_user

    if name == DEFAULT_CHAT_NAME:
        save_chat(user.id, DEFAULT_CHAT_NAME, [])
        set_active_chat(user.id, DEFAULT_CHAT_NAME)
        await query.edit_message_text(f"🗑️ היסטוריית '{DEFAULT_CHAT_NAME}' נוקתה והצ'אט אופס.")
        return
    delete_chat(user.id, name)
    if get_active_chat(user.id) == name:
        ensure_default_chat(user.id)
        set_active_chat(user.id, DEFAULT_CHAT_NAME)
    await query.edit_message_text(f"🗑️ צ'אט '{name}' נמחק.")


# ─────────────────────────────────────────────
#  /help  /list
# ─────────────────────────────────────────────

async def cmd_help_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_authorized(user): return

    ensure_default_chat(user.id)
    active     = get_active_chat(user.id) or "ראשי"
    model_name = load_settings(user.id).get("model", DEFAULT_MODEL)
    tone_name  = load_tone(user.id).get("name", "רגיל")
    chats      = list_chats(user.id)
    memories   = load_memory(user.id)

    model_display = "🧠 AUTO" if model_name == "auto" else f"`{model_name}`"

    msg = (
        "🤖 *פקודות הבוט*\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        "\n"
        "💬 *צ'אטים*\n"
        "`/newchat [שם]` — צ'אט חדש\n"
        "`/chat [שם]` — מעבר לצ'אט\n"
        "`/delchat [שם]` — מחיקת צ'אט\n"
        "`/export [שם]` — ייצוא כקובץ .txt\n"
        "\n"
        "🤖 *מודל ו-AI*\n"
        "`/model` — רשימת מודלים\n"
        "`/model <שם>` — החלפת מודל\n"
        "`/tone [סגנון]` — שינוי סגנון תשובה\n"
        "\n"
        "🧠 *זיכרון*\n"
        "`/remember <עובדה>` — שמירה לזיכרון קבוע\n"
        "`/forget [מספר/טקסט]` — מחיקה מהזיכרון\n"
        "`/memories` — הצגת כל הזיכרונות\n"
        "\n"
        "📋 *היסטוריה*\n"
        "`/undo` — ביטול ההודעה האחרונה\n"
        "`/redo` — שחזור מה שבוטל\n"
        "`/retry` — שליחה מחדש של ההודעה האחרונה\n"
        "`/summarize` — סיכום הצ'אט הפעיל\n"
        "\n"
        "📊 *מידע*\n"
        "`/status` — מצב נוכחי\n"
        "`/stats` — סטטיסטיקות שימוש\n"
        "`/cancel` — ביטול פעולה פעילה\n"
        "`/help` | `/list` — עזרה זו\n"
        "\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        f"💬 צ'אט פעיל: *{active}*  |  {len(chats)} צ'אטים\n"
        f"🤖 מודל: {model_display}  |  🎨 סגנון: {tone_name}\n"
        f"🧠 זיכרונות: {len(memories)}"
    )

    await update.message.reply_text(msg, parse_mode="Markdown")


# ─────────────────────────────────────────────
#  /status
# ─────────────────────────────────────────────

async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_authorized(user): return

    ensure_default_chat(user.id)
    settings    = load_settings(user.id)
    model_name  = settings.get("model", DEFAULT_MODEL)
    active_chat = get_active_chat(user.id)
    chats       = list_chats(user.id)
    history     = load_chat(user.id, active_chat) if active_chat else []

    user_msgs     = len([m for m in history if m["role"] == "user"])
    total_chars   = sum(len(m.get("content", "") if isinstance(m.get("content"), str) else "") for m in history)

    if model_name == "auto":
        model_display = "🧠 AUTO (בחירה אוטומטית)"
    else:
        info = ALL_MODELS.get(model_name, {})
        profile = MODEL_PROFILES.get(model_name, {})
        emoji = profile.get("emoji", "🤖")
        model_display = f"{emoji} {info.get('heb', model_name)} (`{model_name}`)"

    ollama = get_ollama_models()
    ollama_line = f"✅ פעיל ({len(ollama)} מודלים)" if ollama else "❌ לא פעיל"

    lines = [
        "📊 *סטטוס נוכחי*",
        "━━━━━━━━━━━━━━━━━━━",
        f"👤 משתמש: `{user.username or user.id}`",
        f"🤖 מודל: {model_display}",
        "",
        f"💬 צ'אט פעיל: *{active_chat}*",
        f"📝 הודעות בצ'אט: {user_msgs}",
        f"📏 גודל היסטוריה: {total_chars:,} תווים",
        f"📁 סה\"כ צ'אטים: {len(chats)} ({', '.join(chats)})",
        "",
        f"🏠 Ollama: {ollama_line}",
        "━━━━━━━━━━━━━━━━━━━",
    ]
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


# ─────────────────────────────────────────────
#  /retry
# ─────────────────────────────────────────────

async def cmd_retry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_authorized(user): return

    ensure_default_chat(user.id)
    active_chat = get_active_chat(user.id)
    history     = load_chat(user.id, active_chat)

    last_user_msg = None
    while history and history[-1]["role"] == "assistant":
        history.pop()
    if history and history[-1]["role"] == "user":
        last_user_msg = history[-1]["content"]
        history.pop()

    if not last_user_msg:
        await update.message.reply_text("⚠️ אין הודעה קודמת לשליחה מחדש.")
        return

    save_chat(user.id, active_chat, history)

    settings = load_settings(user.id)
    model_name = settings.get("model", DEFAULT_MODEL)
    chat_id = update.effective_chat.id

    await update.message.reply_text(f"🔄 שולח מחדש: _{last_user_msg[:80]}{'...' if len(last_user_msg) > 80 else ''}_", parse_mode="Markdown")

    typing_task = asyncio.create_task(_keep_typing(context.bot, chat_id))
    try:
        current_model = model_name
        if model_name == "auto":
            current_model = select_model_by_keywords(last_user_msg)

        history.append({"role": "user", "content": last_user_msg})
        ai_response = get_ai_response_universal(current_model, history, user_id=user.id)
        history.append({"role": "assistant", "content": ai_response})
        save_chat(user.id, active_chat, history)

        if model_name == "auto":
            profile = MODEL_PROFILES.get(current_model, {})
            emoji = profile.get("emoji", "🤖")
            model_display = ALL_MODELS.get(current_model, {}).get("heb", current_model)
            ai_response += f"\n\n_{emoji} נענה ע\"י: {model_display}_"

    finally:
        typing_task.cancel()
        try:
            await typing_task
        except asyncio.CancelledError:
            pass

    await send_response_with_media(update, context, ai_response)


# ─────────────────────────────────────────────
#  /undo  /redo
# ─────────────────────────────────────────────

def _push_undo(context: ContextTypes.DEFAULT_TYPE, history: list):
    stack = context.user_data.setdefault("undo_stack", [])
    stack.append([m.copy() for m in history])
    if len(stack) > 20:
        stack.pop(0)
    context.user_data["redo_stack"] = []

def _clear_redo(context: ContextTypes.DEFAULT_TYPE):
    context.user_data["redo_stack"] = []
    context.user_data.setdefault("undo_stack", [])


async def cmd_undo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_authorized(user): return

    ensure_default_chat(user.id)
    active_chat = get_active_chat(user.id)
    history     = load_chat(user.id, active_chat)

    if not history:
        await update.message.reply_text("⚠️ ההיסטוריה ריקה — אין מה לבטל.")
        return

    redo_stack = context.user_data.setdefault("redo_stack", [])
    redo_stack.append([m.copy() for m in history])

    removed = []
    if history and history[-1]["role"] == "assistant":
        removed.append(history.pop())
    if history and history[-1]["role"] == "user":
        removed.append(history.pop())

    if not removed:
        redo_stack.pop()
        await update.message.reply_text("⚠️ אין הודעה לביטול.")
        return

    save_chat(user.id, active_chat, history)

    undo_stack = context.user_data.setdefault("undo_stack", [])
    if undo_stack:
        undo_stack.pop()

    user_msg = next((m for m in removed if m["role"] == "user"), None)
    content  = user_msg.get("content", "") if user_msg else ""
    if isinstance(content, list):
        content = " ".join(p.get("text", "") for p in content if isinstance(p, dict))
    preview   = content[:60] + ("..." if len(content) > 60 else "")
    remaining = len([m for m in history if m["role"] == "user"])
    redo_count = len(redo_stack)

    await update.message.reply_text(
        f"↩️ בוטל: _{preview}_\n"
        f"_נשארו {remaining} הודעות_ | "
        f"_ניתן לעשות /redo ({redo_count} שלבים שמורים)_",
        parse_mode="Markdown"
    )


async def cmd_redo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_authorized(user): return

    ensure_default_chat(user.id)
    active_chat = get_active_chat(user.id)
    history     = load_chat(user.id, active_chat)

    redo_stack = context.user_data.get("redo_stack", [])
    if not redo_stack:
        await update.message.reply_text("⚠️ אין מה לשחזר. /redo זמין רק אחרי /undo.")
        return

    undo_stack = context.user_data.setdefault("undo_stack", [])
    undo_stack.append([m.copy() for m in history])
    if len(undo_stack) > 20:
        undo_stack.pop(0)

    restored = redo_stack.pop()
    save_chat(user.id, active_chat, restored)

    user_msgs = [m for m in restored if m["role"] == "user"]
    last_user = user_msgs[-1] if user_msgs else None
    content   = last_user.get("content", "") if last_user else ""
    if isinstance(content, list):
        content = " ".join(p.get("text", "") for p in content if isinstance(p, dict))
    preview   = content[:60] + ("..." if len(content) > 60 else "")
    remaining = len(user_msgs)

    await update.message.reply_text(
        f"↪️ שוחזר: _{preview}_\n"
        f"_נשארו {remaining} הודעות_ | "
        f"_ניתן לעשות /undo ({len(undo_stack)} שלבים שמורים)_",
        parse_mode="Markdown"
    )


# ─────────────────────────────────────────────
#  /summarize
# ─────────────────────────────────────────────

async def cmd_summarize(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_authorized(user): return

    ensure_default_chat(user.id)
    active_chat = get_active_chat(user.id)
    history     = load_chat(user.id, active_chat)

    user_msgs = [m for m in history if m["role"] == "user"]
    if len(user_msgs) < 2:
        await update.message.reply_text("⚠️ אין מספיק הודעות בצ'אט לסיכום.")
        return

    convo_text = ""
    for m in history:
        role  = "👤 משתמש" if m["role"] == "user" else "🤖 בוט"
        content = m.get("content", "")
        if isinstance(content, list):
            content = " ".join(p.get("text", "") for p in content if isinstance(p, dict))
        convo_text += f"{role}: {content}\n\n"

    summarize_prompt = (
        "סכם את השיחה הבאה בעברית בצורה תמציתית וברורה. "
        "כלול: נושאים עיקריים שנדונו, החלטות או מסקנות אם יש, ונקודות חשובות. "
        "אל תוסיף מידע שלא מופיע בשיחה.\n\n"
        f"השיחה:\n{convo_text}"
    )

    chat_id = update.effective_chat.id
    typing_task = asyncio.create_task(_keep_typing(context.bot, chat_id))

    try:
        settings = load_settings(user.id)
        model_name = settings.get("model", DEFAULT_MODEL)
        current_model = "llama-3.3-70b-versatile" if model_name == "auto" else model_name

        summary = get_ai_response_universal(
            current_model,
            [{"role": "user", "content": summarize_prompt}]
        )
    finally:
        typing_task.cancel()
        try:
            await typing_task
        except asyncio.CancelledError:
            pass

    header = f"📋 *סיכום צ'אט '{active_chat}'* ({len(user_msgs)} הודעות)\n\n"
    await send_long_message(update, header + summary)


# ─────────────────────────────────────────────
#  /export
# ─────────────────────────────────────────────

def build_export_text(user_id: int, chat_name: str) -> str:
    history = load_chat(user_id, chat_name)
    lines = [
        f"ייצוא צ'אט: {chat_name}",
        f"תאריך: {datetime.datetime.now().strftime('%d/%m/%Y %H:%M')}",
        f"הודעות: {len([m for m in history if m['role'] == 'user'])}",
        "=" * 40,
        "",
    ]
    for m in history:
        role = "👤 אתה" if m["role"] == "user" else "🤖 בוט"
        content = m.get("content", "")
        if isinstance(content, list):
            content = " ".join(p.get("text", "") for p in content if isinstance(p, dict))
        lines.append(f"{role}:\n{content}\n")
        lines.append("-" * 30)
        lines.append("")
    return "\n".join(lines)


async def cmd_export(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_authorized(user): return

    ensure_default_chat(user.id)

    if context.args:
        await _do_export(update, context, " ".join(context.args))
        return

    chats  = list_chats(user.id)
    active = get_active_chat(user.id)

    buttons = []
    for c in chats:
        label = ("🏠 " if c == DEFAULT_CHAT_NAME else "💬 ")
        label += f"► {c}" if c == active else c
        buttons.append([InlineKeyboardButton(label, callback_data=f"export_chat:{c}")])
    keyboard = InlineKeyboardMarkup(buttons) if buttons else None

    active_line = f"\nפעיל כרגע: *{active}*" if active else ""
    await update.message.reply_text(
        f"📤 *ייצוא צ'אט*{active_line}\n\nבחר צ'אט לייצוא, או כתוב את שמו:",
        parse_mode="Markdown",
        reply_markup=keyboard
    )
    context.user_data["waiting_for"] = "export_chat_name"


async def _do_export(update: Update, context, chat_name: str):
    user = update.effective_user
    chats = list_chats(user.id)
    match = next((c for c in chats if c.lower() == chat_name.lower()), None)
    if not match:
        await update.message.reply_text(f"❌ צ'אט '{chat_name}' לא נמצא.")
        return

    history = load_chat(user.id, match)
    if not history:
        await update.message.reply_text(f"⚠️ הצ'אט '{match}' ריק — אין מה לייצא.")
        return

    export_text = build_export_text(user.id, match)
    buf = io.BytesIO(export_text.encode("utf-8"))
    safe_name = "".join(c if c.isalnum() or c in "-_ " else "_" for c in match).strip()
    filename = f"chat_{safe_name}_{datetime.datetime.now().strftime('%Y%m%d_%H%M')}.txt"
    buf.name = filename

    await update.message.reply_document(
        document=buf,
        filename=filename,
        caption=f"📤 ייצוא צ'אט *{match}* — {len([m for m in history if m['role'] == 'user'])} הודעות",
        parse_mode="Markdown"
    )


async def callback_export_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_name = query.data.split(":", 1)[1]
    context.user_data.pop("waiting_for", None)

    user = query.from_user
    chats = list_chats(user.id)
    match = next((c for c in chats if c == chat_name), None)
    if not match:
        await query.edit_message_text(f"❌ צ'אט '{chat_name}' לא נמצא.")
        return

    history = load_chat(user.id, match)
    if not history:
        await query.edit_message_text(f"⚠️ הצ'אט '{match}' ריק — אין מה לייצא.")
        return

    export_text = build_export_text(user.id, match)
    buf = io.BytesIO(export_text.encode("utf-8"))
    safe_name = "".join(c if c.isalnum() or c in "-_ " else "_" for c in match).strip()
    filename = f"chat_{safe_name}_{datetime.datetime.now().strftime('%Y%m%d_%H%M')}.txt"
    buf.name = filename

    await query.edit_message_text(f"📤 מייצא את '{match}'...")
    await context.bot.send_document(
        chat_id=query.message.chat_id,
        document=buf,
        filename=filename,
        caption=f"📤 ייצוא צ'אט *{match}* — {len([m for m in history if m['role'] == 'user'])} הודעות",
        parse_mode="Markdown"
    )


# ─────────────────────────────────────────────
#  /remember  /forget  /memories
# ─────────────────────────────────────────────

def memory_path(user_id: int) -> str:
    return os.path.join(get_user_dir(user_id), "memory.json")

def load_memory(user_id: int) -> list[str]:
    p = memory_path(user_id)
    if os.path.exists(p):
        try:
            with open(p, 'r', encoding='utf-8') as f:
                return json.load(f)
        except: pass
    return []

def save_memory(user_id: int, memories: list[str]):
    with open(memory_path(user_id), 'w', encoding='utf-8') as f:
        json.dump(memories, f, ensure_ascii=True, indent=2)

def memory_system_prompt(user_id: int) -> str | None:
    memories = load_memory(user_id)
    if not memories:
        return None
    lines = ["מידע שחשוב לזכור על המשתמש:"]
    for i, m in enumerate(memories, 1):
        lines.append(f"{i}. {m}")
    return "\n".join(lines)


async def _do_remember(update: Update, context, fact: str):
    user = update.effective_user
    fact = fact.strip()
    if not fact:
        await update.message.reply_text("❌ לא ניתן לשמור עובדה ריקה.")
        return
    memories = load_memory(user.id)
    if fact.lower() in [m.lower() for m in memories]:
        await update.message.reply_text("ℹ️ עובדה זו כבר שמורה בזיכרון.")
        return
    memories.append(fact)
    save_memory(user.id, memories)
    await update.message.reply_text(
        f"🧠 נשמר בזיכרון!\n`{fact}`\n\n_סה\"כ {len(memories)} עובדות שמורות_",
        parse_mode="Markdown"
    )


async def cmd_remember(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_authorized(user): return

    if not context.args:
        context.user_data["waiting_for"] = "remember_fact"
        await update.message.reply_text(
            "🧠 *מה לזכור?*\nכתוב את העובדה שתרצה שהבוט יזכור:",
            parse_mode="Markdown"
        )
        return

    await _do_remember(update, context, " ".join(context.args))


async def cmd_forget(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_authorized(user): return

    memories = load_memory(user.id)
    if not memories:
        await update.message.reply_text("🧠 הזיכרון ריק — אין מה למחוק.")
        return

    if not context.args:
        buttons = []
        for i, m in enumerate(memories, 1):
            label = f"🗑 {i}. {m[:40]}{'...' if len(m) > 40 else ''}"
            buttons.append([InlineKeyboardButton(label, callback_data=f"forget:{i-1}")])
        buttons.append([InlineKeyboardButton("🗑 מחק הכל", callback_data="forget:all")])
        await update.message.reply_text(
            "🧠 *בחר עובדה למחיקה:*",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(buttons)
        )
        return

    query_text = " ".join(context.args).strip()

    if query_text.isdigit():
        idx = int(query_text) - 1
        if 0 <= idx < len(memories):
            removed = memories.pop(idx)
            save_memory(user.id, memories)
            await update.message.reply_text(
                f"🗑 נמחק מהזיכרון:\n`{removed}`\n\n_נשארו {len(memories)} עובדות_",
                parse_mode="Markdown"
            )
            return
        else:
            await update.message.reply_text(f"❌ מספר {query_text} לא קיים. יש {len(memories)} עובדות.")
            return

    matches = [(i, m) for i, m in enumerate(memories) if query_text.lower() in m.lower()]
    if not matches:
        await update.message.reply_text(f"❌ לא נמצאה עובדה המכילה: '{query_text}'")
        return
    if len(matches) == 1:
        idx, removed = matches[0]
        memories.pop(idx)
        save_memory(user.id, memories)
        await update.message.reply_text(
            f"🗑 נמחק מהזיכרון:\n`{removed}`\n\n_נשארו {len(memories)} עובדות_",
            parse_mode="Markdown"
        )
    else:
        buttons = [[InlineKeyboardButton(f"🗑 {m[:50]}", callback_data=f"forget:{i}")] for i, m in matches]
        await update.message.reply_text(
            f"נמצאו {len(matches)} תוצאות — בחר מה למחוק:",
            reply_markup=InlineKeyboardMarkup(buttons)
        )


async def callback_forget(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = query.from_user
    arg  = query.data.split(":", 1)[1]

    memories = load_memory(user.id)

    if arg == "all":
        await query.edit_message_text(
            "⚠️ האם למחוק את *כל* הזיכרונות?",
            parse_mode="Markdown",
            reply_markup=confirm_keyboard("confirm_forget:all")
        )
        return

    idx = int(arg)
    if 0 <= idx < len(memories):
        fact = memories[idx]
        await query.edit_message_text(
            f"⚠️ האם למחוק מהזיכרון?\n`{fact}`",
            parse_mode="Markdown",
            reply_markup=confirm_keyboard(f"confirm_forget:{idx}")
        )
    else:
        await query.edit_message_text("❌ העובדה כבר לא קיימת.")


async def callback_confirm_forget(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = query.from_user
    arg  = query.data.split(":", 1)[1]

    memories = load_memory(user.id)

    if arg == "all":
        save_memory(user.id, [])
        await query.edit_message_text("🗑 כל הזיכרון נמחק.")
        return

    idx = int(arg)
    if 0 <= idx < len(memories):
        removed = memories.pop(idx)
        save_memory(user.id, memories)
        await query.edit_message_text(
            f"🗑 נמחק:\n`{removed}`\n\n_נשארו {len(memories)} עובדות_",
            parse_mode="Markdown"
        )
    else:
        await query.edit_message_text("❌ העובדה כבר לא קיימת.")


async def cmd_memories(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_authorized(user): return

    memories = load_memory(user.id)
    if not memories:
        await update.message.reply_text(
            "🧠 הזיכרון ריק.\n\nהשתמש ב-`/remember <עובדה>` כדי לשמור מידע.",
            parse_mode="Markdown"
        )
        return

    lines = ["🧠 *הזיכרונות השמורים שלך:*", "━━━━━━━━━━━━━━━━━━━"]
    for i, m in enumerate(memories, 1):
        lines.append(f"{i}\\. {m}")
    lines += ["━━━━━━━━━━━━━━━━━━━", f"_סה\"כ {len(memories)} עובדות_"]
    await update.message.reply_text("\n".join(lines), parse_mode="MarkdownV2")


# ─────────────────────────────────────────────
#  /tone
# ─────────────────────────────────────────────

TONES = {
    "רגיל":    {"desc": "ברירת מחדל — מאוזן",           "prompt": ""},
    "קצר":     {"desc": "תשובות קצרות וענייניות",        "prompt": "ענה בקצרה ובתמציתיות. אל תרחיב מעבר לנחוץ."},
    "מפורט":   {"desc": "הסברים מעמיקים ומלאים",         "prompt": "הסבר בפירוט רב, כלול דוגמאות והקשר."},
    "ידידותי": {"desc": "סגנון חם ונעים",                "prompt": "ענה בסגנון חם, ידידותי ועידוד. השתמש בשפה פשוטה."},
    "רשמי":    {"desc": "שפה מקצועית ורשמית",            "prompt": "ענה בשפה מקצועית ורשמית. הימנע מביטויים מזדמנים."},
    "הומור":   {"desc": "תשובות עם נגיעת הומור",         "prompt": "הוסף נגיעת הומור קלה לתשובות מבלי לפגוע באיכות."},
}

def tone_path(user_id: int) -> str:
    return os.path.join(get_user_dir(user_id), "tone.json")

def load_tone(user_id: int) -> dict:
    p = tone_path(user_id)
    if os.path.exists(p):
        try:
            with open(p, 'r', encoding='utf-8') as f:
                data = json.load(f)
                if isinstance(data, str):
                    t = TONES.get(data, {})
                    return {"name": data, "prompt": t.get("prompt", data)}
                return data
        except: pass
    return {"name": "רגיל", "prompt": ""}

def save_tone(user_id: int, name: str, prompt: str):
    with open(tone_path(user_id), 'w', encoding='utf-8') as f:
        json.dump({"name": name, "prompt": prompt}, f, ensure_ascii=True)

def tone_system_prompt(user_id: int) -> str:
    return load_tone(user_id).get("prompt", "")


async def _apply_tone(update, user_id: int, name: str, prompt: str, edit=False):
    save_tone(user_id, name, prompt)
    text = (
        f"✅ סגנון שונה ל: *{name}*\n"
        + (f"_הוראה: {prompt}_" if prompt else "_ברירת מחדל — ללא הוראה מיוחדת_")
    )
    if edit:
        await update.edit_message_text(text, parse_mode="Markdown")
    else:
        await update.message.reply_text(text, parse_mode="Markdown")


async def cmd_tone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_authorized(user): return

    current = load_tone(user.id)

    if context.args:
        raw = " ".join(context.args).strip()
        if raw in TONES:
            name   = raw
            prompt = TONES[raw]["prompt"]
        else:
            name   = raw[:30] + ("..." if len(raw) > 30 else "")
            prompt = raw
        await _apply_tone(update, user.id, name, prompt)
        return

    buttons = []
    for tone_name, info in TONES.items():
        active = tone_name == current["name"]
        label  = f"{'✅ ' if active else ''}{tone_name} — {info['desc']}"
        buttons.append([InlineKeyboardButton(label, callback_data=f"set_tone:{tone_name}")])

    context.user_data["waiting_for"] = "tone_custom"
    await update.message.reply_text(
        f"🎨 *בחר סגנון תשובה*\nנוכחי: *{current['name']}*\n\n"
        "בחר מהרשימה, *או כתוב סגנון חופשי*, למשל:\n"
        "`ענה כמו פיראט`\n`השתמש רק בנקודות`\n`תמיד פתח עם בדיחה`",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(buttons)
    )


async def callback_set_tone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data.pop("waiting_for", None)
    tone_name = query.data.split(":", 1)[1]
    user = query.from_user
    if tone_name in TONES:
        await _apply_tone(query, user.id, tone_name, TONES[tone_name]["prompt"], edit=True)
    else:
        await query.edit_message_text("❌ סגנון לא מוכר.")


# ─────────────────────────────────────────────
#  /stats
# ─────────────────────────────────────────────

async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_authorized(user): return

    stats = load_stats(user.id)
    total = stats.get("total_messages", 0)

    if total == 0:
        await update.message.reply_text("📊 עדיין אין נתוני שימוש.")
        return

    models = stats.get("models", {})
    sorted_models = sorted(models.items(), key=lambda x: x[1], reverse=True)

    lines = [
        "📊 *סטטיסטיקות שימוש*",
        "━━━━━━━━━━━━━━━━━━━",
        f"📨 סה\"כ הודעות: *{total}*",
        f"📅 שימוש ראשון: {stats.get('first_use', '—')}",
        f"🕐 שימוש אחרון: {stats.get('last_use', '—')}",
        "",
        "🤖 *מודלים בשימוש:*",
    ]

    for model_name, count in sorted_models:
        pct   = round(count / total * 100)
        bar   = "█" * (pct // 10) + "░" * (10 - pct // 10)
        info  = ALL_MODELS.get(model_name, {})
        emoji = MODEL_PROFILES.get(model_name, {}).get("emoji", "🔹")
        heb   = info.get("heb", model_name)
        lines.append(f"{emoji} {heb}: {count} ({pct}%)\n   `{bar}`")

    lines += ["━━━━━━━━━━━━━━━━━━━"]
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


# ─────────────────────────────────────────────
#  /cancel
# ─────────────────────────────────────────────

async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_authorized(user): return
    waiting = context.user_data.pop("waiting_for", None)
    waiting_labels = {
        "newchat_name":    "יצירת צ'אט חדש",
        "delchat_name":    "מחיקת צ'אט",
        "chat_name":       "מעבר צ'אט",
        "export_chat_name":"ייצוא צ'אט",
        "remember_fact":   "שמירת זיכרון",
        "tone_custom":     "שינוי סגנון",
    }
    if waiting:
        label = waiting_labels.get(waiting, waiting)
        await update.message.reply_text(f"❌ הפעולה '{label}' בוטלה.")
    else:
        await update.message.reply_text("אין פעולה פעילה לביטול.")


# ─────────────────────────────────────────────
#  Main
# ─────────────────────────────────────────────

if __name__ == "__main__":
    print("🚀 Bot starting — Auto mode active by default")
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("model",     change_model))
    app.add_handler(CommandHandler("newchat",   cmd_newchat))
    app.add_handler(CommandHandler("chat",      cmd_chat))
    app.add_handler(CommandHandler("delchat",   cmd_delchat))
    app.add_handler(CommandHandler("status",    cmd_status))
    app.add_handler(CommandHandler("stats",     cmd_stats))
    app.add_handler(CommandHandler("retry",     cmd_retry))
    app.add_handler(CommandHandler("undo",      cmd_undo))
    app.add_handler(CommandHandler("redo",      cmd_redo))
    app.add_handler(CommandHandler("summarize", cmd_summarize))
    app.add_handler(CommandHandler("export",    cmd_export))
    app.add_handler(CommandHandler("remember",  cmd_remember))
    app.add_handler(CommandHandler("forget",    cmd_forget))
    app.add_handler(CommandHandler("memories",  cmd_memories))
    app.add_handler(CommandHandler("tone",      cmd_tone))
    app.add_handler(CommandHandler("help",      cmd_help_list))
    app.add_handler(CommandHandler("list",      cmd_help_list))
    app.add_handler(CallbackQueryHandler(callback_switch_chat,     pattern=r"^switch_chat:"))
    app.add_handler(CallbackQueryHandler(callback_del_chat,        pattern=r"^del_chat:"))
    app.add_handler(CallbackQueryHandler(callback_confirm_delchat, pattern=r"^confirm_delchat:"))
    app.add_handler(CallbackQueryHandler(callback_export_chat,     pattern=r"^export_chat:"))
    app.add_handler(CallbackQueryHandler(callback_forget,          pattern=r"^forget:"))
    app.add_handler(CallbackQueryHandler(callback_confirm_forget,  pattern=r"^confirm_forget:"))
    app.add_handler(CallbackQueryHandler(callback_set_tone,        pattern=r"^set_tone:"))
    app.add_handler(CallbackQueryHandler(
        lambda u, c: u.callback_query.answer() or u.callback_query.edit_message_text("❌ בוטל."),
        pattern=r"^confirm:no$"
    ))
    app.add_handler(CommandHandler("cancel",  cmd_cancel))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(filters.PHOTO,        handle_media))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_media))
    app.add_handler(MessageHandler(filters.AUDIO,        handle_media))
    app.add_handler(MessageHandler(filters.VOICE,        handle_media))
    app.add_handler(MessageHandler(filters.VIDEO,        handle_media))
    app.add_handler(MessageHandler(filters.VIDEO_NOTE,   handle_media))
    app.add_handler(MessageHandler(filters.Sticker.ALL,  handle_media))
    app.run_polling()