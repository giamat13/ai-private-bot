import os
import json
import requests
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes

# טעינת הגדרות
load_dotenv()
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
OLLAMA_URL = "http://localhost:11434/api/chat"
MODEL_NAME = "codeqwen:7b-chat-v1.5-q4_K_M"

# יצירת תיקיית היסטוריה אם היא לא קיימת
HISTORY_DIR = "history"
if not os.path.exists(HISTORY_DIR):
    os.makedirs(HISTORY_DIR)

def get_history_file(user_id):
    return os.path.join(HISTORY_DIR, f"chat_{user_id}.json")

def load_user_history(user_id):
    file_path = get_history_file(user_id)
    if os.path.exists(file_path):
        with open(file_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    return []

def save_user_history(user_id, history):
    file_path = get_history_file(user_id)
    with open(file_path, 'w', encoding='utf-8') as f:
        json.dump(history, f, ensure_ascii=False, indent=2)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    user_text = update.message.text

    # טעינה מהכונן הקשיח
    history = load_user_history(user_id)
    
    # הוספת הודעת המשתמש
    history.append({"role": "user", "content": user_text})

    # הגבלת היסטוריה (למשל 20 הודעות אחרונות) כדי לא להכביד על המודל
    if len(history) > 20:
        history = history[-20:]

    waiting_msg = await update.message.reply_text("🤔 חושב... (טוען מהכונן)")

    payload = {
        "model": MODEL_NAME,
        "messages": history,
        "stream": False
    }

    try:
        response = requests.post(OLLAMA_URL, json=payload, timeout=90)
        result = response.json()
        ai_response = result.get("message", {}).get("content", "לא התקבלה תשובה.")

        # הוספת תשובת ה-AI להיסטוריה ושמירה חזרה לכונן
        history.append({"role": "assistant", "content": ai_response})
        save_user_history(user_id, history)
        
        await waiting_msg.edit_text(ai_response)
    except Exception as e:
        await waiting_msg.edit_text(f"שגיאה: {e}")

if __name__ == '__main__':
    if not TELEGRAM_TOKEN:
        print("חסר טוקן ב-.env")
    else:
        print("הבוט רץ ושומר היסטוריה על הכונן...")
        app = Application.builder().token(TELEGRAM_TOKEN).build()
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
        app.run_polling()