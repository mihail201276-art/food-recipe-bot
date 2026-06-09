import asyncio

import httpx
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from services.http_client import shared_async_client
import llm

MEALDB_BASE = "https://www.themealdb.com/api/json/v1/1"


async def generate_ai_recipe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_query = context.user_data.get("ai_query", "любое блюдо")
    await query.edit_message_text("🧠 Думаю над рецептом...")
    recipe = await asyncio.to_thread(llm.generate_recipe, user_query)
    if not recipe or recipe.startswith("⚠"):
        recipe = "Не удалось сгенерировать рецепт. Попробуй позже."
    keyboard = [[InlineKeyboardButton("← Назад к поиску", callback_data="back_search"),
                  InlineKeyboardButton("🏠 Главное меню", callback_data="back_main")]]
    await query.edit_message_text(
        recipe, parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def ai_variation_recipe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    recipe_id = query.data.replace("ai_variation_", "")
    try:
        resp = await shared_async_client.get(f"{MEALDB_BASE}/lookup.php", params={"i": recipe_id})
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        await query.edit_message_text("Ошибка.")
        return
    meals = data.get("meals", [])
    if not meals:
        return
    name = meals[0].get("strMeal", "блюдо")
    await query.edit_message_text(f"🧠 Придумываю вариацию для «{name}»...")
    recipe = await asyncio.to_thread(llm.generate_recipe, f"вариация {name}, другие ингредиенты")
    if not recipe or recipe.startswith("⚠"):
        recipe = "Не удалось сгенерировать рецепт."
    keyboard = [[InlineKeyboardButton("← Назад к рецепту", callback_data=f"recipe_{recipe_id}"),
                  InlineKeyboardButton("🏠 Главное меню", callback_data="back_main")]]
    await query.edit_message_text(
        recipe, parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
