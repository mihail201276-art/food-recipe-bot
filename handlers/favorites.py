import logging
import re
import asyncio
from html import escape

import httpx
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from services.http_client import shared_async_client
from database import add_favorite, remove_favorite, get_favorites, is_favorite, get_rating, update_rating
from llm import split_message

logger = logging.getLogger(__name__)

MEALDB_BASE = "https://www.themealdb.com/api/json/v1/1"


async def add_favorite_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    recipe_id = query.data.replace("fav_add_", "")
    user_id = query.from_user.id
    logger.info("User %s adding favorite recipe %s", user_id, recipe_id)

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
    except Exception:
        logger.exception("Unexpected error in add_favorite_handler")
        await query.edit_message_text("Ошибка.")
        return

    meals = data.get("meals", [])
    if not meals:
        return

    ok = await asyncio.to_thread(add_favorite, user_id, meals[0])
    if ok:
        await query.answer("✅ Добавлено в избранное!", show_alert=True)
    else:
        await query.answer("Уже в избранном.", show_alert=True)

    rate_row = [
        InlineKeyboardButton(
            "⭐" * s + "☆" * (5 - s),
            callback_data=f"rate_{recipe_id}_{s}",
        ) for s in range(1, 6)
    ]
    add_btns = [[InlineKeyboardButton("❌ Удалить из избранного", callback_data=f"fav_del_{recipe_id}")]]
    instr = (meals[0].get("strInstructions") or "")
    if instr and len(instr) > 500:
        add_btns.append([InlineKeyboardButton("📖 Полный рецепт", callback_data=f"full_recipe_{recipe_id}")])
    add_btns.append([InlineKeyboardButton("🌐 Перевести на русский", callback_data=f"translate_{recipe_id}"),
                      InlineKeyboardButton("🔬 Пищевая ценность", callback_data=f"nutri_{recipe_id}")])
    add_btns.append([InlineKeyboardButton("🥛 Без лактозы", callback_data=f"adapt_lactose_{recipe_id}"),
                      InlineKeyboardButton("🔥 Упростить", callback_data=f"adapt_simple_{recipe_id}")])
    add_btns.append([InlineKeyboardButton("👥 На 2 порции", callback_data=f"adapt_portion_{recipe_id}")])
    add_btns.append([InlineKeyboardButton("🔗 Поделиться", callback_data=f"share_{recipe_id}"),
                      InlineKeyboardButton("✨ Вариация", callback_data=f"ai_variation_{recipe_id}")])
    add_btns.append([InlineKeyboardButton("← Назад к поиску", callback_data="back_search"),
                      InlineKeyboardButton("🏠 Главное меню", callback_data="back_main")])
    await query.edit_message_reply_markup(
        reply_markup=InlineKeyboardMarkup([rate_row] + add_btns)
    )


async def remove_favorite_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    recipe_id = query.data.replace("fav_del_", "")
    user_id = query.from_user.id
    logger.info("User %s removing favorite recipe %s", user_id, recipe_id)

    ok = await asyncio.to_thread(remove_favorite, user_id, recipe_id)
    if ok:
        await query.answer("✅ Удалено из избранного!", show_alert=True)
    else:
        await query.answer("Не найдено.", show_alert=True)

    fav_btn = InlineKeyboardButton("❤️ Добавить в избранное", callback_data=f"fav_add_{recipe_id}")
    add_btns = [[fav_btn]]
    instr = ""
    try:
        resp = await shared_async_client.get(f"{MEALDB_BASE}/lookup.php", params={"i": recipe_id})
        resp.raise_for_status()
        data = resp.json()
        if data.get("meals"):
            instr = data["meals"][0].get("strInstructions") or ""
    except Exception:
        pass
    if instr and len(instr) > 500:
        add_btns.append([InlineKeyboardButton("📖 Полный рецепт", callback_data=f"full_recipe_{recipe_id}")])
    add_btns.append([InlineKeyboardButton("🌐 Перевести на русский", callback_data=f"translate_{recipe_id}"),
                      InlineKeyboardButton("🔬 Пищевая ценность", callback_data=f"nutri_{recipe_id}")])
    add_btns.append([InlineKeyboardButton("🥛 Без лактозы", callback_data=f"adapt_lactose_{recipe_id}"),
                      InlineKeyboardButton("🔥 Упростить", callback_data=f"adapt_simple_{recipe_id}")])
    add_btns.append([InlineKeyboardButton("👥 На 2 порции", callback_data=f"adapt_portion_{recipe_id}")])
    add_btns.append([InlineKeyboardButton("🔗 Поделиться", callback_data=f"share_{recipe_id}"),
                      InlineKeyboardButton("✨ Вариация", callback_data=f"ai_variation_{recipe_id}")])
    add_btns.append([InlineKeyboardButton("← Назад к поиску", callback_data="back_search"),
                      InlineKeyboardButton("🏠 Главное меню", callback_data="back_main")])
    await query.edit_message_reply_markup(
        reply_markup=InlineKeyboardMarkup(add_btns)
    )


async def rate_recipe(update: Update, _context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    parts = query.data.split("_")
    recipe_id = parts[1]
    rating = int(parts[2])
    user_id = query.from_user.id
    logger.info("User %s rated recipe %s: %d stars", user_id, recipe_id, rating)

    update_rating(user_id, recipe_id, rating)
    await query.answer(f"⭐ Оценка: {rating}/5")

    stars_str = "⭐" * rating + "☆" * (5 - rating)
    new_text = query.message.caption or query.message.text
    new_text = re.sub(r"⭐ Ваша оценка:.*", f"⭐ Ваша оценка: {stars_str}", new_text)
    if "⭐ Ваша оценка:" not in new_text:
        new_text += f"\n⭐ Ваша оценка: {stars_str}"

    try:
        if query.message.photo:
            await query.edit_message_caption(caption=new_text, parse_mode="HTML")
        else:
            await query.edit_message_text(new_text, parse_mode="HTML")
    except Exception:
        logger.exception("Failed to update rating")


async def my_favorites(update: Update, _context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    logger.info("User %s viewing favorites", user_id)

    favorites = get_favorites(user_id)
    if not favorites:
        await update.message.reply_text("У тебя пока нет избранных рецептов.")
        return

    keyboard = []
    for fav in favorites:
        r = int(fav.get("rating", 0) or 0)
        stars = " " + "⭐" * r if r else ""
        keyboard.append([InlineKeyboardButton(f"{fav['recipe_name']}{stars}", callback_data=f"fav_view_{fav['recipe_id']}")])

    keyboard.append([InlineKeyboardButton("🛒 Список покупок", callback_data="shopping_list")])
    keyboard.append([InlineKeyboardButton("📅 План на неделю", callback_data="meal_plan")])

    await update.message.reply_text(
        f"📚 Твои избранные рецепты ({len(favorites)}):",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def view_favorite(update: Update, _context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    recipe_id = query.data.replace("fav_view_", "")
    user_id = query.from_user.id

    favorites = get_favorites(user_id)
    meal = next((f for f in favorites if f["recipe_id"] == recipe_id), None)
    if not meal:
        await query.edit_message_text("Рецепт не найден.")
        return

    text = f"<b>{escape(meal['recipe_name'])}</b>\n"
    if meal["recipe_category"]:
        text += f"🏷 Категория: {escape(meal['recipe_category'])}\n"
    if meal["recipe_area"]:
        text += f"🌍 Кухня: {escape(meal['recipe_area'])}\n"
    text += f"\n<b>Ингредиенты:</b>\n{escape(meal['ingredients'])}"

    has_image = bool(meal["recipe_image"])
    limit_val = 950 if has_image else 3900
    if len(text) > limit_val:
        text = text[:limit_val] + "..."

    rating = get_rating(user_id, recipe_id)
    stars_str = "⭐" * rating + "☆" * (5 - rating) if rating else "—"
    text += f"\n\n⭐ Ваша оценка: {stars_str}"

    instr = f"\n\n<b>Приготовление:</b>\n{escape(meal['instructions'][:500])}"
    if len(meal["instructions"]) > 500:
        instr += "..."

    youtube = meal.get("youtube_url", "")
    if youtube:
        instr += f"\n\n▶ <a href='{escape(youtube)}'>Смотреть видео</a>"

    free = limit_val - len(text)
    if free > 60:
        text += instr[:free]

    rate_row = [
        InlineKeyboardButton(
            "⭐" * s + "☆" * (5 - s),
            callback_data=f"rate_{recipe_id}_{s}",
        ) for s in range(1, 6)
    ]
    keyboard = [
        rate_row,
        [InlineKeyboardButton("❌ Удалить из избранного", callback_data=f"fav_del_{recipe_id}")],
    ]
    if len(meal.get("instructions", "")) > 500:
        keyboard.append([InlineKeyboardButton("📖 Полный рецепт", callback_data=f"full_recipe_{recipe_id}")])
    keyboard.append([InlineKeyboardButton("🌐 Перевести на русский", callback_data=f"translate_{recipe_id}")])
    keyboard.append([InlineKeyboardButton("🥛 Без лактозы", callback_data=f"adapt_lactose_{recipe_id}"),
                      InlineKeyboardButton("🔥 Упростить", callback_data=f"adapt_simple_{recipe_id}")])
    keyboard.append([InlineKeyboardButton("👥 На 2 порции", callback_data=f"adapt_portion_{recipe_id}")])
    keyboard.append([InlineKeyboardButton("🔗 Поделиться", callback_data=f"share_{recipe_id}"),
                      InlineKeyboardButton("← Назад к избранному", callback_data="back_fav")])
    keyboard.append([InlineKeyboardButton("🏠 Главное меню", callback_data="back_main")])

    try:
        if has_image:
            await query.message.reply_photo(
                photo=meal["recipe_image"], caption=text, parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
            await query.message.delete()
        else:
            await query.edit_message_text(
                text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard)
            )
    except Exception:
        logger.exception("Failed to view favorite")
        await query.edit_message_text("Ошибка при загрузке рецепта.")
