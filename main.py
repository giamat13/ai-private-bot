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

def get_ai_response_universal(model_name, messages):
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
                    {"role": "system", "content": "You are a helpful assistant. Respond in Hebrew. Be accurate."}
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

# מודל עתידי יוכל לשלוח:
#   [IMAGE: https://...]        — תמונה מ-URL
#   [FILE: https://... | שם.pdf] — קובץ מ-URL עם שם אופציונלי
#   [IMAGE_B64: data:image/png;base64,...] — תמונה encoded ישירות

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}
FILE_TAG_RE  = re.compile(r'\[FILE:\s*(https?://\S+?)(?:\s*\|\s*([^\]]+))?\]', re.IGNORECASE)
IMAGE_TAG_RE = re.compile(r'\[IMAGE:\s*(https?://\S+?)\]', re.IGNORECASE)
IMAGE_B64_RE = re.compile(r'\[IMAGE_B64:\s*(data:image/[a-z]+;base64,[A-Za-z0-9+/=]+)\]', re.IGNORECASE)

def parse_ai_response(text: str) -> dict:
    """
    מנתח תשובת מודל ומחזיר:
      {
        "text": str,          # הטקסט נקי ללא תגיות
        "images": [url, ...], # רשימת URLs של תמונות
        "files": [(url, name), ...],  # רשימת קבצים
        "images_b64": [data_uri, ...] # תמונות כ-base64
      }
    """
    images    = [(m.group(1)) for m in IMAGE_TAG_RE.finditer(text)]
    files     = [(m.group(1), m.group(2) or os.path.basename(m.group(1))) for m in FILE_TAG_RE.finditer(text)]
    images_b64 = [(m.group(1)) for m in IMAGE_B64_RE.finditer(text)]

    # זיהוי אוטומטי: URL שמסתיים בסיומת תמונה — גם בלי תגית
    # (למקרה שמודל ישלח URL ישיר)
    url_re = re.compile(r'https?://\S+\.(?:jpg|jpeg|png|gif|webp|bmp)(?:\?\S*)?', re.IGNORECASE)
    for url in url_re.findall(text):
        if url not in images:
            images.append(url)

    # הסר תגיות מהטקסט
    clean = text
    clean = IMAGE_TAG_RE.sub('', clean)
    clean = FILE_TAG_RE.sub('', clean)
    clean = IMAGE_B64_RE.sub('', clean)
    clean = url_re.sub('', clean)  # הסר URL-ים של תמונות שזוהו אוטומטית
    clean = clean.strip()

    return {"text": clean, "images": images, "files": files, "images_b64": images_b64}


async def send_response_with_media(update: Update, context: ContextTypes.DEFAULT_TYPE, ai_response: str):
    """
    שולח תשובת AI כולל תמונות/קבצים אם קיימים.
    תומך ב:
      - [IMAGE: url]
      - [FILE: url | שם]
      - [IMAGE_B64: data:image/...;base64,...]
      - URL ישיר לתמונה בטקסט
    """
    parsed = parse_ai_response(ai_response)
    text   = parsed["text"]

    # שלח טקסט (אם יש)
    if text:
        await send_long_message(update, text)

    # שלח תמונות מ-URL
    for url in parsed["images"]:
        try:
            await update.message.reply_photo(photo=url)
        except Exception as e:
            await update.message.reply_text(f"⚠️ לא ניתן לשלוח תמונה מ-URL:\n{url}\n({e})")

    # שלח תמונות base64
    for data_uri in parsed["images_b64"]:
        try:
            # data:image/png;base64,XXXX  →  bytes
            header, b64data = data_uri.split(",", 1)
            import base64
            img_bytes = base64.b64decode(b64data)
            ext = header.split("/")[1].split(";")[0]  # png / jpeg / etc
            buf = io.BytesIO(img_bytes)
            buf.name = f"image.{ext}"
            await update.message.reply_photo(photo=buf)
        except Exception as e:
            await update.message.reply_text(f"⚠️ לא ניתן לשלוח תמונה (base64): {e}")

    # שלח קבצים מ-URL
    for url, name in parsed["files"]:
        try:
            resp = requests.get(url, timeout=30)
            resp.raise_for_status()
            buf = io.BytesIO(resp.content)
            buf.name = name
            # בדוק אם זו תמונה — אם כן, שלח כתמונה
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

    # הפעל typing indicator מתמשך ברקע
    typing_task = asyncio.create_task(_keep_typing(context.bot, chat_id))

    try:
        current_model = model_name
        if model_name == "auto":
            current_model = select_model_by_keywords(user_text)

        history = load_chat(user.id, active_chat)
        history.append({"role": "user", "content": user_text})

        ai_response = get_ai_response_universal(current_model, history)

        history.append({"role": "assistant", "content": ai_response})
        save_chat(user.id, active_chat, history)

        if model_name == "auto":
            profile = MODEL_PROFILES.get(current_model, {})
            emoji = profile.get("emoji", "🤖")
            model_display = ALL_MODELS.get(current_model, {}).get("heb", current_model)
            ai_response += f"\n\n_{emoji} נענה ע\"י: {model_display}_"

    finally:
        # עצור typing בכל מקרה — גם בשגיאה
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


# ─────────────────────────────────────────────
#  קבלת תמונות / קבצים מהמשתמש
# ─────────────────────────────────────────────

# סיומות שמועברות כתמונה (base64) למודל, השאר כתיאור טקסטואלי
IMAGE_MIME_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp", "image/bmp"}

async def download_telegram_file(context: ContextTypes.DEFAULT_TYPE, file_id: str) -> bytes:
    """מוריד קובץ מ-Telegram ומחזיר bytes."""
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
    """
    בונה הודעת משתמש שתיכנס להיסטוריה.
    אם המודל תומך בתמונות (vision) — מחזיר content list עם image_url.
    אחרת — מחזיר תיאור טקסטואלי.
    """
    text_part = caption.strip() if caption else "תאר/י את הקובץ הזה."

    if base64_uri:
        # פורמט OpenAI vision
        return [
            {"type": "text",      "text": text_part},
            {"type": "image_url", "image_url": {"url": base64_uri}},
        ]
    else:
        # קובץ שאין לו vision — שלח תיאור טקסטואלי
        return f"[המשתמש שלח קובץ: {filename}]\n{text_part}"


async def handle_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handler לתמונות, מסמכים, אודיו, וידאו שנשלחים על ידי המשתמש.
    - תמונות: מומרות ל-base64 ונשלחות למודל כ-vision (אם נתמך).
    - קבצים אחרים: נשלח תיאור טקסטואלי + שם קובץ.
    - מודלי Groq הנוכחיים לא תומכים vision — תישלח הודעת הסבר ברורה.
    """
    user = update.effective_user
    if not is_authorized(user): return

    msg = update.message
    caption = msg.caption or ""

    # --- זהה את סוג המדיה ---
    file_id   = None
    filename  = "קובץ"
    mime_type = "application/octet-stream"
    is_image  = False

    if msg.photo:
        # Telegram שולח מספר גדלים — קח את הגדול ביותר
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
            # לתמונות — נסה llama-4-maverick (יש לו vision), אחרת fallback לכתיבה
            current_model = "llama-4-maverick" if is_image else select_model_by_keywords(caption or filename)

        # --- הורד את הקובץ ---
        file_data = await download_telegram_file(context, file_id)

        # --- בנה הודעה למודל ---
        base64_uri = None
        if is_image:
            base64_uri = bytes_to_base64_uri(file_data, mime_type)

        user_content = build_media_user_message(caption, mime_type, filename, base64_uri)

        # --- שמור בהיסטוריה כטקסט (base64 לא נשמר — גדול מדי) ---
        history = load_chat(user.id, active_chat)
        history_entry = caption if caption else f"[שלח {filename}]"
        history.append({"role": "user", "content": history_entry})

        # --- שלח למודל ---
        # בנה messages עם ה-content האמיתי (כולל base64 אם יש)
        messages_for_ai = load_chat(user.id, active_chat)[:-1]  # היסטוריה ללא הכניסה החדשה
        messages_for_ai.append({"role": "user", "content": user_content})

        ai_response = get_ai_response_universal(current_model, messages_for_ai)

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

        msg += "\n➡️ *שינוי מודל:* `/model <n>`"
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

async def _do_delchat(update: Update, context, name: str):
    user = update.effective_user
    name = name.strip()
    chats = list_chats(user.id)
    match = next((c for c in chats if c.lower() == name.lower()), None)
    if not match:
        await update.message.reply_text(f"❌ צ'אט '{name}' לא נמצא.")
        return
    if match == DEFAULT_CHAT_NAME:
        save_chat(user.id, DEFAULT_CHAT_NAME, [])
        set_active_chat(user.id, DEFAULT_CHAT_NAME)
        await update.message.reply_text(f"🗑️ היסטוריית '{DEFAULT_CHAT_NAME}' נוקתה והצ'אט אופס.")
        return
    delete_chat(user.id, match)
    if get_active_chat(user.id) == match:
        ensure_default_chat(user.id)
        set_active_chat(user.id, DEFAULT_CHAT_NAME)
    await update.message.reply_text(f"🗑️ צ'אט '{match}' נמחק.")

async def callback_del_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    name = query.data.split(":", 1)[1]
    context.user_data.pop("waiting_for", None)
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
    active = get_active_chat(user.id) or "ראשי"
    chats = list_chats(user.id)
    chats_str = ", ".join(chats) if chats else "אין"
    lines = [
        "🤖 *פקודות הבוט*",
        "━━━━━━━━━━━━━━━━━━━",
        "",
        "💬 *ניהול צ'אטים*",
        "`/newchat [שם]` — יצירת צ'אט חדש",
        "`/chat [שם]` — מעבר לצ'אט קיים",
        "`/delchat [שם]` — מחיקת צ'אט",
        "",
        "🤖 *מודל AI*",
        "`/model` — הצגת כל המודלים הזמינים",
        "`/model <שם>` — החלפת מודל",
        "",
        "📋 *כללי*",
        "`/cancel` — ביטול פעולה נוכחית",
        "`/help` | `/list` — הצגת עזרה זו",
        "",
        "━━━━━━━━━━━━━━━━━━━",
        f"_פעיל: *{active}* | צ'אטים: {chats_str}_",
        "_💡 AUTO פעיל — הבוט בוחר מודל לכל שאלה_",
    ]
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


# ─────────────────────────────────────────────
#  /cancel
# ─────────────────────────────────────────────

async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_authorized(user): return
    waiting = context.user_data.pop("waiting_for", None)
    waiting_labels = {
        "newchat_name": "יצירת צ'אט חדש",
        "delchat_name": "מחיקת צ'אט",
        "chat_name":    "מעבר צ'אט",
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
    app.add_handler(CommandHandler("model",   change_model))
    app.add_handler(CommandHandler("newchat", cmd_newchat))
    app.add_handler(CommandHandler("chat",    cmd_chat))
    app.add_handler(CommandHandler("delchat", cmd_delchat))
    app.add_handler(CommandHandler("help",    cmd_help_list))
    app.add_handler(CommandHandler("list",    cmd_help_list))
    app.add_handler(CallbackQueryHandler(callback_switch_chat, pattern=r"^switch_chat:"))
    app.add_handler(CallbackQueryHandler(callback_del_chat,    pattern=r"^del_chat:"))
    app.add_handler(CommandHandler("cancel",  cmd_cancel))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    # קבלת מדיה מהמשתמש
    app.add_handler(MessageHandler(filters.PHOTO,                          handle_media))
    app.add_handler(MessageHandler(filters.Document.ALL,                   handle_media))
    app.add_handler(MessageHandler(filters.AUDIO,                          handle_media))
    app.add_handler(MessageHandler(filters.VOICE,                          handle_media))
    app.add_handler(MessageHandler(filters.VIDEO,                          handle_media))
    app.add_handler(MessageHandler(filters.VIDEO_NOTE,                     handle_media))
    app.add_handler(MessageHandler(filters.Sticker.ALL,                    handle_media))
    app.run_polling()