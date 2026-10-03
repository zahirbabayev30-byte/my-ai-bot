import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
import telegram
from telegram.ext import ApplicationBuilder, MessageHandler, filters, ContextTypes, CommandHandler
import requests

# ==================== БЕЗОПАСНО ====================
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
API_KEY        = os.environ.get("API_KEY")
API_URL        = "https://llmcod.ru/v1/chat/completions"
MODEL_NAME     = "qwen3-coder-30b-a3b-instruct"
# ===================================================

# --- Фейковый веб-сервер для Render (чтобы он видел порт) ---
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/plain')
        self.end_headers()
        self.wfile.write(b"Bot is running!")

def run_health_check():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(('0.0.0.0', port), HealthCheckHandler)
    server.serve_forever()
# -----------------------------------------------------------

async def start(update: telegram.Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Привет! Я твой ИИ-бот 🤖\nНапиши любой вопрос — отвечу.")

async def handle_message(update: telegram.Update, context: ContextTypes.DEFAULT_TYPE):
    user_text = update.message.text
    chat_id = update.effective_chat.id

    await context.bot.send_chat_action(chat_id=chat_id,
                                       action=telegram.constants.ChatAction.TYPING)

    headers = {"Authorization": f"Bearer {API_KEY}",
               "Content-Type": "application/json"}
    data = {
        "model": MODEL_NAME,
        "messages": [
            {"role": "system", "content": "Ты дружелюбный ИИ-помощник. Отвечай кратко на русском."},
            {"role": "user", "content": user_text},
        ],
        "temperature": 0.7,
    }

    try:
        r = requests.post(API_URL, headers=headers, json=data, timeout=30)
        result = r.json()
        if "choices" in result:
            reply = result["choices"][0]["message"]["content"].strip()
        else:
            reply = "⚠ Ошибка нейросети: " + str(result)[:200]
    except Exception as e:
        reply = f"⚠ Ошибка соединения: {e}"

    await update.message.reply_text(reply)

if __name__ == "__main__":
    if not TELEGRAM_TOKEN or not API_KEY:
        print("❌ Ошибка: не заданы переменные окружения TELEGRAM_TOKEN и API_KEY")
        exit(1)

    # Запускаем фейковый сервер в отдельном потоке
    threading.Thread(target=run_health_check, daemon=True).start()

    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    print("✅ Бот запущен на сервере...")
    app.run_polling()
