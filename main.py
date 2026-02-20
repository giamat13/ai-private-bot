import os
import json
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
# OpenAI הוסר מהרשימה הפעילה אך נשאר כספק נתמך בליבה
ALL_MODELS = {
    "llama-3.3-70b-versatile": {"provider": "groq", "heb": "מצוינת", "speed": "מהיר"},
    "llama-3.1-8b-instant": {"provider": "groq", "heb": "מהירה", "speed": "מיידי"},
    "llama-3.3-70b": {"provider": "cerebras", "heb": "חזקה", "speed": "מהירות שיא"},
    "mistral-small-latest": {"provider": "mistral", "heb": "איכותית", "speed": "מהיר"},
    "gemini-1.5-flash": {"provider": "gemini", "heb": "הכי טובה לעברית", "speed": "מהיר"},
    "together-qwen": {"provider": "together", "heb": "חכמה מאוד", "speed": "מהיר"},
    "auto": {"provider": "system", "heb": "בחירה אוטומטית", "speed": "משתנה"}
}

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
        return False
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

# --- ליבת ה-AI ---

def get_ai_response_universal(model_name, messages):
    ollama_local = get_ollama_models()
    if model_name in ollama_local:
        try:
            res = requests.post("http://localhost:11434/api/chat", 
                                json={"model": model_name, "messages": messages, "stream": False}, timeout=120)
            return res.json().get("message", {}).get("content", "שגיאה באולמה")
        except Exception as e: return f"❌ אולמה לא זמין: {e}"

    # אם המודל לא קיים ב-ALL_MODELS (כמו ה-GPT שהסרנו), הוא לא ירוץ
    info = ALL_MODELS.get(model_name)
    if not info: return "❌ מודל לא מוכר או מושבת."
    
    provider = info["provider"]
    key_env_name = f"{provider.upper()}_API_KEY"
    raw_keys = os.getenv(key_env_name, "")
    api_keys = [k.strip() for k in raw_keys.split(",") if k.strip()]
    
    if not api_keys: 
        return f"❌ חסר טוקן עבור: {key_env_name}"

    for key in api_keys:
        try:
            if provider == "groq": url = "https://api.groq.com/openai/v1/chat/completions"
            elif provider == "cerebras": url = "https://api.cerebras.ai/v1/chat/completions"
            elif provider == "mistral": url = "https://api.mistral.ai/v1/chat/completions"
            elif provider == "together": url = "https://api.together.xyz/v1/chat/completions"
            elif provider == "openai": url = "https://api.openai.com/v1/chat/completions"
            elif provider == "gemini": url = f"https://generativelanguage.googleapis.com/v1beta/openai/chat/completions?key={key}"
            
            headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
            actual_model = model_name
            if provider == "together": actual_model = "Qwen/Qwen2.5-72B-Instruct-Turbo"
            
            payload = {
                "model": actual_model,
                "messages": [{"role": "system", "content": "You are a helpful assistant. Respond in Hebrew."}] + messages
            }
            res = requests.post(url, headers=headers, json=payload, timeout=45)
            if res.status_code == 200: return res.json()['choices'][0]['message']['content']
        except: continue
    return "❌ תקלה בחיבור לספק ה-AI."

# --- טיפול בהודעות ---

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_authorized(user): return
    user_text = update.message.text
    user_dir = os.path.join(HISTORY_DIR, str(user.id))
    os.makedirs(user_dir, exist_ok=True)

    model_name = DEFAULT_MODEL
    settings_path = os.path.join(user_dir, "settings.json")
    if os.path.exists(settings_path):
        with open(settings_path, 'r') as f: model_name = json.load(f).get('model', DEFAULT_MODEL)

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    decision_prompt = f"Categorize: Internet needed? (YES/NO). Task difficulty? (SIMPLE/HARD). Question: {user_text}"
    decision = get_ai_response_universal("llama-3.1-8b-instant", [{"role": "user", "content": decision_prompt}])
    
    current_model = model_name
    final_query = user_text

    if model_name == "auto":
        # חזרה לשימוש ב-Llama למשימות קשות
        if "HARD" in decision.upper(): 
            current_model = "llama-3.3-70b-versatile"
        else: 
            current_model = "gemini-1.5-flash"

    if "YES" in decision.upper():
        search_data = search_tavily(user_text)
        final_query = f"מידע מהאינטרנט:\n{search_data}\n\nשאלה: {user_text}"

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

    await update.message.reply_text(ai_response)

async def change_model(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_authorized(user): return
    ollama_list = get_ollama_models()
    
    if not context.args:
        msg = "🤖 **מודלים פעילים:**\n"
        msg += "\n".join([f"🔹 `{m}` - {info['heb']}" for m, info in ALL_MODELS.items()])
        if ollama_list:
            msg += "\n\n🏠 **Ollama:**\n" + "\n".join([f"🔹 `{m}`" for m in ollama_list])
        await update.message.reply_text(msg, parse_mode="Markdown")
        return

    new_model = context.args[0]
    if new_model in ALL_MODELS or new_model in ollama_list:
        user_dir = os.path.join(HISTORY_DIR, str(user.id))
        os.makedirs(user_dir, exist_ok=True)
        with open(os.path.join(user_dir, "settings.json"), 'w') as f: json.dump({"model": new_model}, f)
        await update.message.reply_text(f"✅ עברת ל-`{new_model}`")

if __name__ == '__main__':
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("model", change_model))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    print("🚀 Bot is Online! OpenAI is currently disabled.")
    app.run_polling()