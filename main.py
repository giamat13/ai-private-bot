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
ALLOWED_USERNAME = "Giamat13"

if not os.path.exists(HISTORY_DIR):
    os.makedirs(HISTORY_DIR)

def get_history_file(user_id, chat_name):
    return os.path.join(HISTORY_DIR, f"chat_{user_id}_{chat_name}.json")

def load_history(user_id, chat_name):
    file_path = get_history_file(user_id, chat_name)
    if os.path.exists(file_path):
        with open(file_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    return []

def save_history(user_id, chat_name, history):
    file_path = get_history_file(user_id, chat_name)
    with open(file_path, 'w', encoding='utf-8') as f:
        json.dump(history, f, ensure_ascii=False, indent=2)

async def is_authorized(update: Update):
    user = update.effective_user
    if user.username != ALLOWED_USERNAME:
        await update.message.reply_text("🚫 מצטער, אין לך הרשאה.")
        return False
    return True

async def list_chats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_authorized(update): return
    user_id = update.message.from_user.id
    files = [f for f in os.listdir(HISTORY_DIR) if f.startswith(f"chat_{user_id}_")]
    chat_names = [f.replace(f"chat_{user_id}_", "").replace(".json", "") for f in files]
    await update.message.reply_text("📁 צ'אטים:\n" + "\n".join([f"• {name}" for name in chat_names]))

async def change_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_authorized(update): return
    if not context.args: return
    new_chat_name = context.args[0].lower()
    context.user_data['active_chat'] = new_chat_name
    await update.message.reply_text(f"✅ עברנו ל: {new_chat_name}")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_authorized(update): return
    
    user_id = update.message.from_user.id
    user_text = update.message.text
    chat_name = context.user_data.get('active_chat', 'default')
    
    history = load_history(user_id, chat_name)
    history.append({"role": "user", "content": user_text})
    if len(history) > 20: history = history[-20:]

    try:
        # שליחת הבקשה ל-Ollama ללא הודעת "חושב"
        response = requests.post(OLLAMA_URL, json={
            "model": MODEL_NAME,
            "messages": history,
            "stream": False
        }, timeout=120)
        
        ai_response = response.json().get("message", {}).get("content", "שגיאה")
        
        # שמירת התשובה ושליחה ישירה למשתמש
        history.append({"role": "assistant", "content": ai_response})
        save_history(user_id, chat_name, history)
        
        await update.message.reply_text(ai_response)
        
    except Exception as e:
        await update.message.reply_text(f"שגיאה: {e}")

if __name__ == '__main__':
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("chat", change_chat))
    app.add_handler(CommandHandler("list", list_chats))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    print(f"הבוט רץ עבור {ALLOWED_USERNAME} (ללא הודעות המתנה)")
    app.run_polling()