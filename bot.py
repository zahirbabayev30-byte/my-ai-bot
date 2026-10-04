import os
import threading
import base64
import requests
from http.server import BaseHTTPRequestHandler, HTTPServer

import telegram
from telegram.ext import (
    ApplicationBuilder, MessageHandler, filters,
    ContextTypes, CommandHandler
)
from telegram import ReplyKeyboardMarkup, KeyboardButton

# ==================== НАСТРОЙКИ ====================
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")

# Groq (для текста)
API_KEY    = os.environ.get("API_KEY")
API_URL    = "https://api.groq.com/openai/v1/chat/completions"
MODEL_NAME = "llama-3.3-70b-versatile"

# Cloudflare (для картинок)
CF_ACCOUNT_ID = os.environ.get("CF_ACCOUNT_ID")
CF_API_TOKEN  = os.environ.get("CF_API_TOKEN")
CF_MODEL      = "@cf/black-forest-labs/flux-1-schnell"

SYSTEM_PROMPT = (
    "Ты умный, дружелюбный и полезный ИИ-помощник. "
    "Отвечай подробно, с примерами, если уместно. "
    "Общайся на русском языке."
)

user_histories = {}
user_modes = {}
user_last_prompt = {}
MAX_HISTORY = 10
# ====================================================

def main_keyboard():
    buttons = [
        [KeyboardButton("💬 Чат"), KeyboardButton("🎨 Картинка")],
        [KeyboardButton("🔁 Перегенерировать"), KeyboardButton("📚 Википедия")],
        [KeyboardButton("🧹 Очистить")],
    ]
    return ReplyKeyboardMarkup(buttons, resize_keyboard=True)

class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/plain')
        self.end_headers()
        self.wfile.write(b"Bot is running!")

def run_health_check():
    port = int(os.environ.get("PORT", 10000))
    HTTPServer(('0.0.0.0', port), HealthCheckHandler).serve_forever()

def search_wikipedia(query, lang="ru"):
    try:
        url = f"https://{lang}.wikipedia.org/w/api.php"
        params = {"action": "query", "list": "search",
                  "srsearch": query, "format": "json", "srlimit": 2}
        r = requests.get(url, params=params, timeout=10,
                         headers={"User-Agent": "MyAI/1.0"})
        items = r.json().get("query", {}).get("search", [])
        if not items: return None
        out = "📚 Из Википедии:\n"
        for it in items:
            sn = it["snippet"].replace('<span class="searchmatch">', '').replace('</span>', '')
            out += f"• {it['title']}: {sn[:200]}...\n"
        return out
    except Exception:
        return None

async def send_image(update, context, prompt):
    await context.bot.send_chat_action(
        chat_id=update.effective_chat.id,
        action=telegram.constants.ChatAction.UPLOAD_PHOTO
    )
    if not CF_ACCOUNT_ID or not CF_API_TOKEN:
        await update.message.reply_text("⚠ Не настроен генератор картинок.",
                                        reply_markup=main_keyboard())
        return
    url = f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT_ID}/ai/run/{CF_MODEL}"
    headers = {"Authorization": f"Bearer {CF_API_TOKEN}",
               "Content-Type": "application/json"}
    # Убрали num_steps — Cloudflare его больше не принимает
    payload = {"prompt": prompt}
    try:
        r = requests.post(url, headers=headers, json=payload, timeout=60)
        result = r.json()
        if "result" in result and "image" in result["result"]:
            image_bytes = base64.b64decode(result["result"]["image"])
            await update.message.reply_photo(photo=image_bytes,
                                             caption=f"🎨 {prompt}",
                                             reply_markup=main_keyboard())
        else:
            err = result.get("errors", "Неизвестная ошибка")
            await update.message.reply_text(f"⚠ Ошибка Cloudflare: {str(err)[:300]}",
                                            reply_markup=main_keyboard())
    except Exception as e:
        await update.message.reply_text(f"⚠ Ошибка: {e}", reply_markup=main_keyboard())

async def start(update: telegram.Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_histories[chat_id] = []
    user_modes[chat_id] = "chat"
    await update.message.reply_text(
        "Привет! Я твой умный ИИ-бот 🤖\n\n"
        "Просто напиши мне или выбери действие кнопкой внизу 👇",
        reply_markup=main_keyboard()
    )

async def handle_message(update: telegram.Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    chat_id = update.effective_chat.id
    if chat_id not in user_histories: user_histories[chat_id] = []
    if chat_id not in user_modes: user_modes[chat_id] = "chat"
    if chat_id not in user_last_prompt: user_last_prompt[chat_id] = None

    if text == "💬 Чат":
        user_modes[chat_id] = "chat"
        await update.message.reply_text("💬 Режим чата. Пиши вопрос!",
                                        reply_markup=main_keyboard())
        return
    if text == "🎨 Картинка":
        user_modes[chat_id] = "image"
        await update.message.reply_text(
            "🎨 Опиши, что нарисовать.\n\n"
            "❌ Плохо: «кот»\n"
            "✅ Хорошо: «реалистичный белый кот в скафандре, космос, звёзды, 8k»",
            reply_markup=main_keyboard())
        return
    if text == "🔁 Перегенерировать":
        last = user_last_prompt.get(chat_id)
        if not last:
            await update.message.reply_text("❓ Пока нечего перегенерировать.",
                                            reply_markup=main_keyboard())
            return
        await update.message.reply_text(f"🔁 Перегенерирую: «{last}»...",
                                        reply_markup=main_keyboard())
        await send_image(update, context, last)
        return
    if text == "📚 Википедия":
        user_modes[chat_id] = "wiki"
        await update.message.reply_text("📚 Введи запрос.", reply_markup=main_keyboard())
        return
    if text == "🧹 Очистить":
        user_histories[chat_id] = []
        user_modes[chat_id] = "chat"
        user_last_prompt[chat_id] = None
        await update.message.reply_text("🧹 Всё очищено!", reply_markup=main_keyboard())
        return

    if user_modes[chat_id] == "image":
        words = text.split()
        # Лимит снижен: от 3 слов и от 15 символов
        if len(words) < 3 or len(text) < 15:
            await update.message.reply_text(
                "⚠ Слишком короткий запрос.\n\n"
                "❌ Плохо: «кот»\n"
                "✅ Хорошо: «реалистичный белый кот, космос, звёзды, 8k»",
                reply_markup=main_keyboard())
            return
        user_modes[chat_id] = "chat"
        user_last_prompt[chat_id] = text
        await send_image(update, context, text)
        return

    if user_modes[chat_id] == "wiki":
        user_modes[chat_id] = "chat"
        result = search_wikipedia(text)
        await update.message.reply_text(result if result else "Ничего не нашёл.",
                                        reply_markup=main_keyboard())
        return

    await context.bot.send_chat_action(chat_id=chat_id,
                                       action=telegram.constants.ChatAction.TYPING)
    history = user_histories[chat_id]
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages += history[-MAX_HISTORY:]
    messages.append({"role": "user", "content": text})
    headers = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}
    data = {"model": MODEL_NAME, "messages": messages, "temperature": 0.7, "max_tokens": 1000}
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
