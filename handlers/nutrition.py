import json
import asyncio
import logging
from html import escape

import httpx
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from services.http_client import shared_async_client
from database import log_meal, get_daily_nutrition, get_recent_meals
from utils.keyboards import NUTRITION_KEYBOARD
import llm

logger = logging.getLogger(__name__)

MEALDB_BASE = "https://www.themealdb.com/api/json/v1/1"


async def nutrition_menu(update: Update, _context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🥗 <b>Нутрициолог</b>\n\n"
        "• 🔬 Анализ рецепта — отправь название или ссылку\n"
        "• 📊 Дневной отчёт — калории, БЖУ, вода\n"
        "• 💧 Вода +250мл — добавить воды\n"
        "• ➕ Записать приём пищи — вручную",
        parse_mode="HTML", reply_markup=NUTRITION_KEYBOARD,
    )


async def nutrition_analyze_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["state"] = "nutrition_analyze"
    await update.message.reply_text("Отправь название блюда или описание продуктов:")


async def nutrition_analyze(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    await update.message.reply_chat_action("typing")
    result = await asyncio.to_thread(llm.analyze_nutrition, text, "")
    if not result or "⚠" in result:
        await update.message.reply_text("Не удалось проанализировать.")
        return
    try:
        data = json.loads(result)
        lines = [
            f"<b>🔬 Пищевая ценность: {escape(text[:50])}</b>",
            f"🔥 Калории: <b>{data.get('calories', '?')} ккал</b>",
            f"🥩 Белки: {data.get('protein', '?')}г",
            f"🧈 Жиры: {data.get('fat', '?')}г",
            f"🍚 Углеводы: {data.get('carbs', '?')}г",
            f"🌾 Клетчатка: {data.get('fiber', '?')}г",
            f"🍽 Порций: {data.get('servings', 1)}",
        ]
        await update.message.reply_text("\n".join(lines), parse_mode="HTML")
        cals = data.get("calories", 0)
        prot = data.get("protein", 0)
        fat = data.get("fat", 0)
        carbs = data.get("carbs", 0)
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Записать в дневник",
                callback_data=f"nutri_log_{text[:30]}|{cals}|{prot}|{fat}|{carbs}"),
        ]])
        await update.message.reply_text("Записать в дневник питания?", reply_markup=kb)
    except Exception:
        await update.message.reply_text(f"📊 Анализ:\n{result}")


async def nutri_log_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    parts = query.data.replace("nutri_log_", "").split("|")
    food_name = parts[0]
    cals = float(parts[1]) if len(parts) > 1 else 0
    prot = float(parts[2]) if len(parts) > 2 else 0
    fat = float(parts[3]) if len(parts) > 3 else 0
    carbs = float(parts[4]) if len(parts) > 4 else 0
    user_id = query.from_user.id
    today = __import__("datetime").date.today().isoformat()
    log_meal(user_id, today, "анализ", food_name, cals, prot, fat, carbs)
    await query.message.reply_text("✅ Записано в дневник!")


async def nutrition_daily_report(update: Update, _context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    today = __import__("datetime").date.today().isoformat()
    day = get_daily_nutrition(user_id, today)
    meals = get_recent_meals(user_id, today)
    lines = [f"<b>📊 Дневной отчёт за {today}</b>",
             f"🔥 Калории: <b>{day['calories']} ккал</b>",
             f"🥩 Белки: {day['protein']}г  🧈 Жиры: {day['fat']}г  🍚 Углеводы: {day['carbs']}г",
             f"💧 Вода: {day['water_ml']}мл",
             ""]
    if meals:
        lines.append("🍽 Приёмы пищи:")
        for m in meals[:5]:
            lines.append(f"• {m['food_name'][:40]} — {m['calories']}ккал")
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


async def nutrition_add_water(update: Update, _context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    today = __import__("datetime").date.today().isoformat()
    log_meal(user_id, today, "вода", "Вода", water_ml=250)
    total = get_daily_nutrition(user_id, today)["water_ml"]
    await update.message.reply_text(f"💧 +250мл воды! Всего сегодня: {total}мл")


async def nutrition_log_meal_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["state"] = "nutrition_manual"
    await update.message.reply_text(
        "Напиши в формате:\n"
        "<i>Название блюда, калории, белки, жиры, углеводы</i>\n\n"
        "Например: <i>Овсянка, 300, 10, 5, 50</i>",
        parse_mode="HTML",
    )


async def nutrition_log_manual(update: Update, context: ContextTypes.DEFAULT_TYPE):
    parts = update.message.text.split(",")
    if len(parts) < 2:
        await update.message.reply_text("Неверный формат. Используй: Название, калории, белки, жиры, углеводы")
        return
    food = parts[0].strip()
    cals = float(parts[1].strip()) if len(parts) > 1 else 0
    prot = float(parts[2].strip()) if len(parts) > 2 else 0
    fat = float(parts[3].strip()) if len(parts) > 3 else 0
    carbs = float(parts[4].strip()) if len(parts) > 4 else 0
    user_id = update.effective_user.id
    today = __import__("datetime").date.today().isoformat()
    log_meal(user_id, today, "ручной", food, cals, prot, fat, carbs)
    await update.message.reply_text(f"✅ {food} — {cals}ккал записано!")


async def recipe_nutrition(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    recipe_id = query.data.replace("nutri_", "")
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
        await query.message.reply_text("Ошибка.")
        return
    meals = data.get("meals", [])
    if not meals:
        return
    meal = meals[0]
    name = meal.get("strMeal", "")
    ings = []
    for i in range(1, 21):
        ing = meal.get(f"strIngredient{i}")
        meas = meal.get(f"strMeasure{i}")
        if ing and ing.strip():
            ings.append(f"{ing} — {meas if meas else ''}")
    ingredients_text = "\n".join(ings)
    instructions = meal.get("strInstructions", "")
    await query.message.reply_text(f"🔬 Анализирую «{name}»...")
    result = await asyncio.to_thread(llm.analyze_nutrition, name, ingredients_text, instructions)
    try:
        data = json.loads(result)
        lines = [
            f"<b>🔬 {escape(name)}</b>",
            f"🔥 Калории: <b>{data.get('calories', '?')} ккал</b>",
            f"🥩 Белки: {data.get('protein', '?')}г",
            f"🧈 Жиры: {data.get('fat', '?')}г",
            f"🍚 Углеводы: {data.get('carbs', '?')}г",
            f"🌾 Клетчатка: {data.get('fiber', '?')}г",
            f"🍽 Порций: {data.get('servings', 1)}",
        ]
        await query.message.reply_text("\n".join(lines), parse_mode="HTML")
    except Exception:
        await query.message.reply_text(f"📊 {result}")

