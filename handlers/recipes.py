import logging
from html import escape

import httpx
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from services.http_client import shared_async_client
from database import is_favorite, get_rating
from llm import split_message

logger = logging.getLogger(__name__)

MEALDB_BASE = "https://www.themealdb.com/api/json/v1/1"


async def show_recipe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    recipe_id = query.data.replace("recipe_", "")
    user_id = query.from_user.id
    logger.info("User %s viewing recipe %s", user_id, recipe_id)

    try:
        resp = await shared_async_client.get(f"{MEALDB_BASE}/lookup.php", params={"i": recipe_id})
        resp.raise_for_status()
        data = resp.json()
    except httpx.TimeoutException:
        await query.edit_message_text("⏱️ Таймаут запроса.")
        return
    except httpx.HTTPStatusError as e:
        logger.error("API error: %s", e.response.status_code)
        await query.edit_message_text("⚠️ Временная ошибка API.")
        return
    except Exception as e:
        logger.exception("Unexpected error in show_recipe")
        await query.edit_message_text("Ошибка загрузки рецепта.")
        return

    meals = data.get("meals", [])
    if not meals:
        await query.edit_message_text("Рецепт не найден.")
        return

    meal = meals[0]
    name = meal.get("strMeal") or "Unknown"
    category = meal.get("strCategory") or ""
    area = meal.get("strArea") or ""
    instructions = meal.get("strInstructions") or ""
    image = meal.get("strMealThumb") or ""
    youtube = meal.get("strYoutube") or ""

    await query.message.delete()
    if image:
        await context.bot.send_photo(chat_id=query.message.chat_id, photo=image, caption=f"<b>{escape(name)}</b>", parse_mode="HTML")

    parts = []
    if category:
        parts.append(f"🏷 Категория: {escape(category)}")
    if area:
        parts.append(f"🌍 Кухня: {escape(area)}")

    ings = []
    for i in range(1, 21):
        ing = meal.get(f"strIngredient{i}")
        meas = meal.get(f"strMeasure{i}")
        if ing and ing.strip():
            ings.append(f"• {escape(ing.strip())}" + (f" — {escape(meas.strip())}" if meas else ""))
    if ings:
        parts.append("<b>Ингредиенты:</b>\n" + "\n".join(ings))

    if instructions:
        parts.append(f"<b>Приготовление:</b>\n{escape(instructions)}")

    fav = is_favorite(user_id, recipe_id)
    if fav:
        rating = get_rating(user_id, recipe_id)
        parts.append(f"⭐ Ваша оценка: {'⭐' * rating}{'☆' * (5 - rating)}" if rating else "⭐ Ваша оценка: —")

    if youtube:
        parts.append(f"▶ <a href='{escape(youtube)}'>Смотреть видео</a>")

    text = "\n\n".join(parts)

    keyboard = []
    if fav:
        keyboard.append([InlineKeyboardButton("⭐" * s + "☆" * (5 - s), callback_data=f"rate_{recipe_id}_{s}") for s in range(1, 6)])
        keyboard.append([InlineKeyboardButton("❌ Удалить из избранного", callback_data=f"fav_del_{recipe_id}")])
    else:
        keyboard.append([InlineKeyboardButton("❤️ Добавить в избранное", callback_data=f"fav_add_{recipe_id}")])
    keyboard.append([InlineKeyboardButton("🌐 Перевести на русский", callback_data=f"translate_{recipe_id}"),
                      InlineKeyboardButton("🔬 Пищевая ценность", callback_data=f"nutri_{recipe_id}")])
    keyboard.append([InlineKeyboardButton("🥛 Без лактозы", callback_data=f"adapt_lactose_{recipe_id}"),
                      InlineKeyboardButton("🔥 Упростить", callback_data=f"adapt_simple_{recipe_id}")])
    keyboard.append([InlineKeyboardButton("👥 На 2 порции", callback_data=f"adapt_portion_{recipe_id}")])
    keyboard.append([InlineKeyboardButton("🔗 Поделиться", callback_data=f"share_{recipe_id}"),
                      InlineKeyboardButton("✨ Вариация", callback_data=f"ai_variation_{recipe_id}")])
    keyboard.append([InlineKeyboardButton("← Назад к поиску", callback_data="back_search"),
                      InlineKeyboardButton("🏠 Главное меню", callback_data="back_main")])

    chunks = split_message(text, 4000)
    for i, chunk in enumerate(chunks):
        is_last = (i == len(chunks) - 1)
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text=chunk,
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(keyboard) if is_last else None,
        )


async def full_recipe_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    recipe_id = query.data.replace("full_recipe_", "")
    logger.info("Full recipe requested for %s", recipe_id)

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
        logger.exception("Unexpected error in full_recipe_handler")
        await query.message.reply_text("Ошибка загрузки рецепта.")
        return

    meals = data.get("meals", [])
    if not meals:
        await query.message.reply_text("Рецепт не найден.")
        return

    instructions = meals[0].get("strInstructions") or ""
    name = meals[0].get("strMeal") or "Рецепт"
    text = f"<b>{escape(name)}</b>\n\n<b>Приготовление:</b>\n{escape(instructions)}"

    if len(text) <= 4000:
        await query.message.reply_text(text, parse_mode="HTML")
    else:
        for i in range(0, len(text), 4000):
            part = text[i:i+4000]
            await query.message.reply_text(part, parse_mode="HTML")


async def random_recipe(update: Update, _context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    logger.info("User %s requested random recipe", user_id)

    if update.callback_query:
        await update.callback_query.answer()

    try:
        resp = await shared_async_client.get(f"{MEALDB_BASE}/random.php")
        resp.raise_for_status()
        data = resp.json()
    except httpx.TimeoutException:
        target = update.callback_query.message if update.callback_query else update.message
        await target.reply_text("⏱️ Таймаут запроса.")
        return
    except httpx.HTTPStatusError as e:
        logger.error("API error: %s", e.response.status_code)
        target = update.callback_query.message if update.callback_query else update.message
        await target.reply_text("⚠️ Временная ошибка API.")
        return
    except Exception:
        logger.exception("Unexpected error in random_recipe")
        target = update.callback_query.message if update.callback_query else update.message
        await target.reply_text("Ошибка загрузки. Попробуй позже.")
        return

    meals = data.get("meals", [])
    if not meals:
        target = update.callback_query.message if update.callback_query else update.message
        await target.reply_text("Не удалось получить рецепт.")
        return

    recipe_id = meals[0]["idMeal"]
    keyboard = [[InlineKeyboardButton("🍽 Показать рецепт", callback_data=f"recipe_{recipe_id}")],
                [InlineKeyboardButton("🎲 Ещё раз", callback_data="random_")]]
    target = update.callback_query.message if update.callback_query else update.message
    await target.reply_text(
        f"🎲 Случайный рецепт: <b>{escape(meals[0]['strMeal'])}</b>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def adapt_recipe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    parts = query.data.split("_", 2)
    adapt_type = parts[1]
    recipe_id = parts[2]
    user_id = query.from_user.id
    logger.info("User %s adapting recipe %s: %s", user_id, recipe_id, adapt_type)

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
        logger.exception("Unexpected error in adapt_recipe")
        await query.message.reply_text("Ошибка загрузки рецепта.")
        return

    meals = data.get("meals", [])
    if not meals:
        await query.message.reply_text("Рецепт не найден.")
        return

    import llm as llm_module

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
            ings.append(f"{ing} — {meas}" if meas else ing)

    recipe_text = (
        f"Recipe: {name}\nCategory: {category}\nCuisine: {area}\n"
        f"Ingredients:\n" + "\n".join(ings) + "\n"
        f"Instructions:\n{instructions}"
    )

    prompts = {
        "lactose": "Адаптируй рецепт для безлактозной диеты. Замени молочные продукты на безлактозные альтернативы. Сохрани структуру.",
        "simple": "Упрости рецепт: сократи ингредиенты до минимума, замени редкие продукты на доступные, упрости шаги. Сохрани структуру.",
        "portion": "Пересчитай рецепт на 2 порции. Укажи новые количества ингредиентов. Сохрани структуру.",
    }
    system = f"Ты кулинарный помощник. {prompts.get(adapt_type, '')} Ответ дай на русском."

    await context.bot.send_chat_action(chat_id=query.message.chat_id, action="typing")
    import asyncio
    reply = await asyncio.to_thread(llm_module.get_llm_response, recipe_text, system)
    for part in split_message(reply):
        await query.message.reply_text(part)


async def share_recipe(update: Update, _context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    recipe_id = query.data.replace("share_", "")
    url = f"https://www.themealdb.com/meal/{recipe_id}"
    share_link = f"https://t.me/share/url?url={url}&text=🍽 Рецепт:"
    await query.message.reply_text(
        f"🔗 Поделись рецептом:\n{share_link}"
    )
