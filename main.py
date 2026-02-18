import os
import json
import requests
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, MessageHandler, CommandHandler, filters, ContextTypes

# טעינת הגדרות
load_dotenv()
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
OLLAMA_URL = "http://localhost:11434/api/chat"
MODEL_NAME = "codeqwen:7b-chat-v1.5-q4_K_M"
HISTORY_DIR = "history"
USERS_FILE = "allowed_users.txt"

# יצירת תיקיית ההיסטוריה מראש אם היא לא קיימת
if not os.path.exists(HISTORY_DIR):
    os.makedirs(HISTORY_DIR)

# --- ניהול משתמשים ---

def get_allowed_users():
    if not os.path.exists(USERS_FILE):
        with open(USERS_FILE, 'w', encoding='utf-8') as f:
            f.write("Giamat13\n")
        return ["Giamat13"]
    with open(USERS_FILE, 'r', encoding='utf-8') as f:
        return [line.strip() for line in f.readlines() if line.strip()]

async def is_authorized(update: Update):
    user = update.effective_user
    if not user: return False
    allowed_list = get_allowed_users()
    if user.username in allowed_list or str(user.id) in allowed_list:
        return True
    if update.message:
        await update.message.reply_text(f"🚫 גישה חסומה (ID: {user.id})")
    return False

# --- ניהול קבצים (תיקון השגיאה כאן) ---

def get_history_file(user_id, chat_name):
    return os.path.join(HISTORY_DIR, f"chat_{user_id}_{chat_name}.json")

def load_history(user_id, chat_name):
    file_path = get_history_file(user_id, chat_name)
    # תיקון: אם הקובץ לא קיים, מחזירים רשימה ריקה במקום לקרוס
    if not os.path.exists(file_path):
        return []
    
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except Exception:
        return []

def save_history(user_id, chat_name, history):
    file_path = get_history_file(user_id, chat_name)
    # הקובץ נוצר כאן אוטומטית ברגע ששומרים
    with open(file_path, 'w', encoding='utf-8') as f:
        json.dump(history, f, ensure_ascii=False, indent=2)

def get_active_chat(user_id):
    path = os.path.join(HISTORY_DIR, f"settings_{user_id}.json")
    if not os.path.exists(path):
        return 'default'
    try:
        with open(path, 'r') as f:
            return json.load(f).get('active_chat', 'default')
    except:
        return 'default'

def set_active_chat(user_id, chat_name):
    path = os.path.join(HISTORY_DIR, f"settings_{user_id}.json")
    with open(path, 'w') as f:
        json.dump({'active_chat': chat_name}, f)

# --- טיפול בהודעות ---

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_authorized(update): return
    
    user_id = update.message.from_user.id
    user_text = update.message.text
    chat_name = get_active_chat(user_id)
    
    history = load_history(user_id, chat_name)
    history.append({"role": "user", "content": user_text})
    
    if len(history) > 20:
        history = history[-20:]

    full_messages = [
        {"role": "system", "content": "You are a helpful assistant. You must remember and refer to previous details in this chat history."}
    ] + history

    try:
        response = requests.post(OLLAMA_URL, json={
            "model": MODEL_NAME,
            "messages": full_messages,
            "stream": False
        }, timeout=150)
        
        ai_response = response.json().get("message", {}).get("content", "שגיאה בתשובה")
        
        history.append({"role": "assistant", "content": ai_response})
        save_history(user_id, chat_name, history)
        
        await update.message.reply_text(ai_response)
    except Exception as e:
        await update.message.reply_text(f"שגיאה: {e}")

# --- פקודות ---

async def change_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_authorized(update): return
    if not context.args:
        await update.message.reply_text("שימוש: /chat <שם>")
        return
    user_id = update.message.from_user.id
    new_chat_name = context.args[0].lower()
    set_active_chat(user_id, new_chat_name)
    await update.message.reply_text(f"🔄 עברנו לצ'אט '{new_chat_name}'.")

async def list_chats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_authorized(update): return
    user_id = update.message.from_user.id
    files = [f for f in os.listdir(HISTORY_DIR) if f.startswith(f"chat_{user_id}_")]
    chat_names = [f.replace(f"chat_{user_id}_", "").replace(".json", "") for f in files]
    current = get_active_chat(user_id)
    await update.message.reply_text(f"📁 צ'אטים:\n" + "\n".join([f"• {n}" for n in chat_names]) + f"\n\n📍 פעיל: {current}")

if __name__ == '__main__':
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("chat", change_chat))
    app.add_handler(CommandHandler("list", list_chats))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    print("הבוט רץ! התיקון לשגיאת הקבצים הופעל.")
    app.run_polling()