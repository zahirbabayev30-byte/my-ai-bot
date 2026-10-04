import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import quote

import telegram
from telegram.ext import (
    ApplicationBuilder, MessageHandler, filters,
    ContextTypes, CommandHandler
)
from telegram import ReplyKeyboardMarkup, KeyboardButton
import requests

# ==================== НАСТРОЙКИ ====================
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
API_KEY        = os.environ.get("API_KEY")
API_URL        = "https://llmcod.ru/v1/chat/completions"
MODEL_NAME     = "qwen3-coder-30b-a3b-instruct"

SYSTEM_PROMPT = (
    "Ты умный, дружелюбный и полезный ИИ-помощник. "
    "Отвечай подробно, с примерами, если уместно. "
    "Общайся на русском языке."
)

user_histories = {}
MAX_HISTORY = 10
user_modes = {}   # какой режим выбран у пользователя: chat / image / wiki
# ====================================================


# ---------- Кнопки меню ----------
def main_keyboard():
    buttons = [
        [KeyboardButton("💬 Чат"), KeyboardButton("🎨 Картинка")],
        [KeyboardButton("📚 Википедия"), KeyboardButton("🧹 Очистить")],
    ]
    return ReplyKeyboardMarkup(buttons, resize_keyboard=True)


# ---------- Фейковый сервер для Render ----------
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/plain')
        self.end_headers()
        self.wfile.write(b"Bot is running!")


def run_health_check():
    port = int(os.environ.get("PORT", 10000))
    HTTPServer(('0.0.0.0', port), HealthCheckHandler).serve_forever()


# ---------- Поиск в Википедии ----------
def search_wikipedia(query, lang="ru"):
    try:
        url = f"https://{lang}.wikipedia.org/w/api.php"
        params = {"action": "query", "list": "search",
                  "srsearch": query, "format": "json", "srlimit": 2}
        r = requests.get(url, params=params, timeout=10,
                         headers={"User-Agent": "MyAI/1.0"})
        items = r.json().get("query", {}).get("search", [])
        if not items:
            return None
        out = "📚 Из Википедии:\n"
        for it in items:
            sn = it["snippet"].replace('<span class="searchmatch">', '').replace('</span>', '')
            out += f"• {it['title']}: {sn[:200]}...\n"
        return out
    except Exception:
        return None


# ---------- /start ----------
async def start(update: telegram.Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_histories[chat_id] = []
    user_modes[chat_id] = "chat"
    await update.message.reply_text(
        "Привет! Я твой умный ИИ-бот 🤖\n\n"
        "Просто напиши мне или выбери действие кнопкой внизу 👇",
        reply_markup=main_keyboard()
    )


# ---------- Основной обработчик ----------
async def handle_message(update: telegram.Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    chat_id = update.effective_chat.id

    # инициализация
    if chat_id not in user_histories:
        user_histories[chat_id] = []
    if chat_id not in user_modes:
        user_modes[chat_id] = "chat"

    # --- Обработка кнопок ---
    if text == "💬 Чат":
        user_modes[chat_id] = "chat"
        await update.message.reply_text(
            "💬 Режим чата включён. Пиши вопрос!",
            reply_markup=main_keyboard()
        )
        return

    if text == "🎨 Картинка":
        user_modes[chat_id] = "image"
        await update.message.reply_text(
            "🎨 Опиши, что нарисовать (одним сообщением).",
            reply_markup=main_keyboard()
        )
        return

    if text == "📚 Википедия":
        user_modes[chat_id] = "wiki"
        await update.message.reply_text(
            "📚 Введи запрос для поиска в Википедии.",
            reply_markup=main_keyboard()
        )
        return

    if text == "🧹 Очистить":
        user_histories[chat_id] = []
        user_modes[chat_id] = "chat"
        await update.message.reply_text(
            "🧹 История очищена!",
            reply_markup=main_keyboard()
        )
        return

    # --- Режим КАРТИНКА ---
    if user_modes[chat_id] == "image":
        user_modes[chat_id] = "chat"
        await context.bot.send_chat_action(
            chat_id=chat_id,
            action=telegram.constants.ChatAction.UPLOAD_PHOTO
        )
        enhanced = text + ", high quality, detailed, 4k, photorealistic"
        encoded = quote(enhanced)
        image_url = (
            f"https://image.pollinations.ai/prompt/{encoded}"
            f"?width=1280&height=1280&nologo=true&enhance=true"
        )
        try:
            await update.message.reply_photo(photo=image_url,
                                             caption=f"🎨 {text}",
                                             reply_markup=main_keyboard())
        except Exception as e:
            await update.message.reply_text(f"⚠ Не удалось нарисовать: {e}",
                                            reply_markup=main_keyboard())
        return

    # --- Режим ВИКИПЕДИЯ ---
    if user_modes[chat_id] == "wiki":
        user_modes[chat_id] = "chat"
        result = search_wikipedia(text)
        await update.message.reply_text(
            result if result else "Ничего не нашёл в Википедии.",
            reply_markup=main_keyboard()
        )
        return

    # --- Режим ЧАТ (по умолчанию) ---
    await context.bot.send_chat_action(chat_id=chat_id,
                                       action=telegram.constants.ChatAction.TYPING)

    history = user_histories[chat_id]
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages += history[-MAX_HISTORY:]
    messages.append({"role": "user", "content": text})

    headers = {"Authorization": f"Bearer {API_KEY}",
               "Content-Type": "application/json"}
    data = {"model": MODEL_NAME, "messages": messages,
            "temperature": 0.7, "max_tokens": 1000}

    try:
        r = requests.post(API_URL, headers=headers, json=data, timeout=60)
        result = r.json()
        if "choices" in result:
            reply = result["choices"][0]["message"]["content"].strip()
        else:
            wiki = search_wikipedia(text)
            reply = wiki if wiki else "⚠ Ошибка нейросети."
    except Exception as e:
        reply = f"⚠ Ошибка соединения: {e}"

    history.append({"role": "user", "content": text})
    history.append({"role": "assistant", "content": reply})

    await update.message.reply_text(reply, reply_markup=main_keyboard())


if __name__ == "__main__":
    if not TELEGRAM_TOKEN or not API_KEY:
        print("❌ Ошибка: не заданы переменные окружения")
        exit(1)

    threading.Thread(target=run_health_check, daemon=True).start()

    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("✅ Бот запущен на сервере...")
    app.run_polling()
