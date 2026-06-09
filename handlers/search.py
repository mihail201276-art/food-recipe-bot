import logging
import re
import asyncio

import httpx
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from services.http_client import shared_async_client
import llm

logger = logging.getLogger(__name__)

MEALDB_BASE = "https://www.themealdb.com/api/json/v1/1"
MAX_RESULTS = int(__import__("os").getenv("MAX_RESULTS", "8"))


async def search_prompt(update: Update, _context: ContextTypes.DEFAULT_TYPE):
    logger.info("User %s requested search", update.effective_user.id)
    await update.message.reply_text("Напиши название блюда (например, борщ, паста с курицей):")


async def search_recipes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = (context.user_data.pop("voice_text", None) or update.message.text).strip()
    user_id = update.effective_user.id
    logger.info("User %s searching for: %s", user_id, query)

    if not query:
        await update.message.reply_text("Пожалуйста, введи название блюда.")
        return

    await update.message.reply_chat_action("typing")

    if re.search(r"[а-яА-ЯёЁ]", query):
        logger.info("Non-Latin query detected, translating: %s", query)
        system = "Переведи название блюда на английский. Ответь только одним-двумя словами, без пояснений."
        translated = await asyncio.to_thread(llm.get_llm_response, query, system)
        if translated and not translated.startswith("⚠"):
            logger.info("Translated '%s' -> '%s'", query, translated)
            query = translated.strip().lower()

    try:
        resp = await shared_async_client.get(f"{MEALDB_BASE}/search.php", params={"s": query})
        resp.raise_for_status()
        data = resp.json()
    except httpx.TimeoutException:
        await update.message.reply_text("⏱️ Таймаут запроса. Попробуй позже.")
        return
    except httpx.HTTPStatusError:
        await update.message.reply_text("⚠️ Временная ошибка API.")
        return
    except Exception as e:
        logger.exception("Unexpected error in search_recipes")
        await update.message.reply_text("Ошибка при поиске. Попробуй позже.")
        return

    meals = data.get("meals", [])
    if not meals:
        logger.info("No results for query, generating via AI: %s", query)
        await update.message.reply_text("🧠 В TheMealDB ничего нет, придумываю рецепт через ИИ...")
        recipe = await asyncio.to_thread(llm.generate_recipe, query)
        if recipe and not recipe.startswith("⚠"):
            keyboard = [[InlineKeyboardButton("🔍 Новый поиск", callback_data="back_search"),
                          InlineKeyboardButton("🏠 Главное меню", callback_data="back_main")]]
            await update.message.reply_text(
                recipe, parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
        else:
            await update.message.reply_text("Не удалось сгенерировать рецепт. Попробуй другое название.")
        return

    meals = meals[:MAX_RESULTS]
    keyboard = []
    for meal in meals:
        name = meal.get("strMeal", "Unknown")
        keyboard.append([InlineKeyboardButton(name, callback_data=f"recipe_{meal['idMeal']}")])

    await update.message.reply_text(
        f"Найдено рецептов: {len(meals)}\nВыбери рецепт:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
