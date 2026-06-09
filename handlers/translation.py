import logging
import asyncio

import httpx
from telegram import Update
from telegram.ext import ContextTypes

from services.http_client import shared_async_client
from database import get_profile, check_and_increment_translation, get_translation, save_translation
from llm import get_llm_response, split_message

logger = logging.getLogger(__name__)

MEALDB_BASE = "https://www.themealdb.com/api/json/v1/1"
COCKTAILDB_BASE = "https://www.thecocktaildb.com/api/json/v1/1"
TRANSLATE_DAILY_LIMIT = int(__import__("os").getenv("TRANSLATE_DAILY_LIMIT", "20"))
PREMIUM_DAILY_LIMIT = int(__import__("os").getenv("PREMIUM_DAILY_LIMIT", "100"))


async def translate_recipe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    recipe_id = query.data.replace("translate_", "")
    user_id = query.from_user.id
    logger.info("User %s translating recipe %s", user_id, recipe_id)

    profile = get_profile(user_id)
    limit = PREMIUM_DAILY_LIMIT if profile.get("premium") else TRANSLATE_DAILY_LIMIT
    if not check_and_increment_translation(user_id, limit):
        await query.message.reply_text(f"⚠️ Лимит переводов на сегодня ({limit} шт.) исчерпан. Попробуй завтра.")
        return

    cached = get_translation(recipe_id, "ru")
    if cached:
        logger.info("Translation cache hit for recipe %s", recipe_id)
        text = f"<b>🌐 Перевод на русский:</b>\n\n{cached}"
        for chunk in split_message(text, 4000):
            await query.message.reply_text(chunk, parse_mode="HTML")
        return

    try:
        resp = await shared_async_client.get(f"{MEALDB_BASE}/lookup.php", params={"i": recipe_id})
        resp.raise_for_status()
        data = resp.json()
    except httpx.TimeoutException:
        await query.message.reply_text("⏱️ Таймаут запроса.")
        return
    except httpx.HTTPStatusError as e:
        logger.error("API error: %s", e.response.status_code)
        await query.message.reply_text("⚠️ Временная ошибка API.")
        return
    except Exception:
        logger.exception("Unexpected error in translate_recipe")
        await query.message.reply_text("Ошибка загрузки рецепта.")
        return

    meals = data.get("meals", [])
    if not meals:
        await query.message.reply_text("Рецепт не найден.")
        return

    meal = meals[0]
    name = meal.get("strMeal") or "Unknown"
    category = meal.get("strCategory") or ""
    area = meal.get("strArea") or ""
    instructions = meal.get("strInstructions") or ""

    ings = []
    for i in range(1, 21):
        ing = meal.get(f"strIngredient{i}")
        meas = meal.get(f"strMeasure{i}")
        if ing and ing.strip():
            ings.append(f"{ing}{' — ' + meas if meas else ''}")

    recipe_text = (
        f"Recipe: {name}\n"
        f"Category: {category}\n"
        f"Cuisine: {area}\n"
        f"Ingredients:\n" + "\n".join(ings) + "\n"
        f"Instructions:\n{instructions}"
    )

    system_prompt = (
        "Ты переводчик рецептов. Переведи следующий рецепт с английского на русский. "
        "Сохрани структуру: название, категория, кухня, ингредиенты, инструкция. "
        "Используй HTML-теги <b> для заголовков. Ответ дай только на русском, без комментариев."
    )

    await context.bot.send_chat_action(chat_id=query.message.chat_id, action="typing")
    reply = await asyncio.to_thread(get_llm_response, recipe_text, system_prompt)
    save_translation(recipe_id, "ru", reply)

    text = f"<b>🌐 Перевод на русский:</b>\n\n{reply}"
    for chunk in split_message(text, 4000):
        await query.message.reply_text(chunk, parse_mode="HTML")


async def translate_cocktail(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    drink_id = query.data.replace("transdrink_", "")
    user_id = query.from_user.id

    profile = get_profile(user_id)
    limit = PREMIUM_DAILY_LIMIT if profile.get("premium") else TRANSLATE_DAILY_LIMIT
    if not check_and_increment_translation(user_id, limit):
        await query.message.reply_text(f"⚠️ Лимит переводов на сегодня ({limit} шт.) исчерпан.")
        return

    try:
        resp = await shared_async_client.get(f"{COCKTAILDB_BASE}/lookup.php", params={"i": drink_id})
        resp.raise_for_status()
        data = resp.json()
    except httpx.TimeoutException:
        await query.message.reply_text("⏱️ Таймаут запроса.")
        return
    except httpx.HTTPStatusError as e:
        logger.error("API error: %s", e.response.status_code)
        await query.message.reply_text("⚠️ Временная ошибка API.")
        return
    except Exception:
        logger.exception("Unexpected error in translate_cocktail")
        await query.message.reply_text("Ошибка.")
        return

    drinks = data.get("drinks", [])
    if not drinks:
        return

    d = drinks[0]
    name = d.get("strDrink") or ""
    category = d.get("strCategory") or ""
    alcoholic = d.get("strAlcoholic") or ""
    glass = d.get("strGlass") or ""
    instructions = d.get("strInstructions") or ""

    ings = []
    for i in range(1, 16):
        ing = d.get(f"strIngredient{i}")
        meas = d.get(f"strMeasure{i}")
        if ing and ing.strip():
            ings.append(f"{ing}{' — ' + meas if meas else ''}")

    drink_text = (
        f"Drink: {name}\n"
        f"Category: {category}\n"
        f"Type: {alcoholic}\n"
        f"Glass: {glass}\n"
        f"Ingredients:\n" + "\n".join(ings) + "\n"
        f"Instructions:\n{instructions}"
    )

    system = (
        "Ты переводчик рецептов коктейлей. Переведи на русский. "
        "Сохрани структуру. Используй <b> для заголовков. Только русский, без комментариев."
    )

    await context.bot.send_chat_action(chat_id=query.message.chat_id, action="typing")
    reply = await asyncio.to_thread(get_llm_response, drink_text, system)

    text = f"<b>🌐 Перевод на русский:</b>\n\n{reply}"
    for chunk in split_message(text, 4000):
        await query.message.reply_text(chunk, parse_mode="HTML")
