import asyncio
import base64
import io
import logging

from telegram import Update
from telegram.ext import ContextTypes

from llm import transcribe_audio, _call_proxyapi_vision, split_message
from services.rate_limiter import rate_limiter

logger = logging.getLogger(__name__)


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if not rate_limiter.is_allowed(user_id):
        await update.message.reply_text("🚀 Слишком много запросов. Подожди немного.")
        return

    logger.info("User %s sent voice message", user_id)

    file = await update.message.voice.get_file()
    file_path = f"/tmp/voice_{user_id}.ogg"
    await file.download_to_drive(file_path)

    await update.message.reply_chat_action("typing")
    text = await asyncio.to_thread(transcribe_audio, file_path)
    if not text:
        await update.message.reply_text("Не удалось распознать голос.")
        return

    logger.info("Transcribed: %s", text)
    context.user_data["state"] = None
    context.user_data["voice_text"] = text.strip()
    from handlers.search import search_recipes
    await search_recipes(update, context)


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if not rate_limiter.is_allowed(user_id):
        await update.message.reply_text("🚀 Слишком много запросов. Подожди немного.")
        return

    logger.info("User %s sent photo", user_id)

    await update.message.reply_chat_action("typing")

    file = await update.message.photo[-1].get_file()
    buf = io.BytesIO()
    try:
        await file.download_to_memory(buf)
        buf.seek(0)
        b64 = base64.b64encode(buf.read()).decode()
        data_uri = f"data:image/jpeg;base64,{b64}"
    except Exception as e:
        logger.error("Failed to download photo: %s", e)
        await update.message.reply_text("Не удалось загрузить фото.")
        return

    prompt = "Посмотри на фото продуктов. Предложи 3-5 блюд, которые можно из них приготовить. Напиши на русском, кратко."
    reply = await asyncio.to_thread(_call_proxyapi_vision, data_uri, prompt)
    if not reply:
        await update.message.reply_text("Не удалось проанализировать фото.")
        return

    for part in split_message(reply):
        await update.message.reply_text(part)
