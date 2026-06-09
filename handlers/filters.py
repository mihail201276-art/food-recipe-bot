import logging

import httpx
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from services.http_client import shared_async_client

logger = logging.getLogger(__name__)

MEALDB_BASE = "https://www.themealdb.com/api/json/v1/1"
MAX_RESULTS = int(__import__("os").getenv("MAX_RESULTS", "8"))


async def filter_menu(update: Update, _context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    logger.info("User %s opened filter menu", user_id)
    keyboard = [
        [InlineKeyboardButton("🍝 По категории", callback_data="flist_cat")],
        [InlineKeyboardButton("🌍 По кухне", callback_data="flist_cuisine")],
        [InlineKeyboardButton("🥕 По ингредиенту", callback_data="flist_ing")],
        [InlineKeyboardButton("← Назад", callback_data="back_main")],
    ]
    target = update.callback_query.message if update.callback_query else update.message
    text = "Выбери тип фильтра:"
    if update.callback_query:
        await update.callback_query.answer()
        await target.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        await target.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))


async def filter_list(update: Update, _context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    filter_type = data.replace("flist_", "")
    param_key = {"cat": "c", "cuisine": "a", "ing": "i"}.get(filter_type)
    if not param_key:
        return

    try:
        resp = await shared_async_client.get(f"{MEALDB_BASE}/list.php", params={param_key: "list"})
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
        logger.exception("Unexpected error in filter_list")
        await query.message.reply_text("Ошибка загрузки списка.")
        return

    items = data.get("meals", [])
    if not items:
        await query.message.reply_text("Список пуст.")
        return

    key_map = {"cat": "strCategory", "cuisine": "strArea", "ing": "strIngredient"}
    item_key = key_map[filter_type]
    prefix_map = {"cat": "fcat_", "cuisine": "fcuisine_", "ing": "fing_"}
    prefix = prefix_map[filter_type]

    keyboard = []
    for item in items[:30]:
        name = item.get(item_key, "")
        if name:
            keyboard.append([InlineKeyboardButton(name, callback_data=f"{prefix}{name}")])
    keyboard.append([InlineKeyboardButton("← Назад к фильтрам", callback_data="filter_menu")])

    await query.edit_message_text(
        f"Выбери {filter_type}:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def filter_results(update: Update, _context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    prefix_map = {"fcat_": ("c", "strCategory"), "fcuisine_": ("a", "strArea"), "fing_": ("i", "strIngredient")}
    param_key = None
    value = None
    for p, (pk, _) in prefix_map.items():
        if data.startswith(p):
            param_key = pk
            value = data[len(p):]
            break
    if not param_key:
        return

    try:
        resp = await shared_async_client.get(f"{MEALDB_BASE}/filter.php", params={param_key: value})
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
        logger.exception("Unexpected error in filter_results")
        await query.message.reply_text("Ошибка фильтрации.")
        return

    meals = data.get("meals", [])
    if not meals:
        await query.message.reply_text("Ничего не найдено.")
        return

    meals = meals[:MAX_RESULTS]
    keyboard = []
    for meal in meals:
        name = meal.get("strMeal", "Unknown")
        keyboard.append([InlineKeyboardButton(name, callback_data=f"recipe_{meal['idMeal']}")])
    keyboard.append([InlineKeyboardButton("← Назад к фильтрам", callback_data="filter_menu")])

    await query.edit_message_text(
        f"Найдено: {len(meals)}\nВыбери рецепт:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
