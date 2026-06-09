import logging
import random
from html import escape

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from database import get_favorites
from llm import split_message

logger = logging.getLogger(__name__)

MAX_RESULTS = int(__import__("os").getenv("MAX_RESULTS", "8"))


async def cook_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info("User %s opened cook", update.effective_user.id)
    context.user_data["state"] = "cook"
    await update.message.reply_text(
        "Напиши, какие продукты у тебя есть (например: курица, рис, лук, морковь):"
    )


async def cook_suggest(update: Update, _context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    user_id = update.effective_user.id
    logger.info("User %s cook ingredients: %s", user_id, text)

    await update.message.reply_chat_action("typing")

    import asyncio
    import llm as llm_module

    system = (
        "У тебя есть следующие продукты. Предложи 3-5 блюд, которые можно из них приготовить. "
        "Каждое блюдо напиши с новой строки, начиная с «•». "
        "После названия в скобках укажи ключевые слова для поиска на английском. "
        "Пример: • Куриный суп с рисом (chicken rice soup)\nОтвет дай только на русском."
    )
    reply = await asyncio.to_thread(llm_module.get_llm_response, text, system)
    await update.message.reply_text(reply)


async def shopping_list(update: Update, _context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    favorites = get_favorites(user_id)
    if not favorites:
        await query.message.reply_text("У тебя нет избранных рецептов.")
        return

    all_ings = {}
    for fav in favorites:
        ings_text = fav.get("ingredients", "")
        for line in ings_text.split("\n"):
            line = line.strip()
            if line:
                name = line.split(" – ")[0].strip().lower() if " – " in line else line.lower()
                if name:
                    all_ings[name] = line

    if not all_ings:
        await query.message.reply_text("Нет ингредиентов для списка.")
        return

    lines = [f"🛒 <b>Список покупок</b> ({len(favorites)} рецептов):\n"]
    for ing in sorted(all_ings.values()):
        lines.append(f"• {escape(ing)}")

    text = "\n".join(lines)
    for part in split_message(text):
        await query.message.reply_text(part, parse_mode="HTML")


async def meal_plan(update: Update, _context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    favorites = get_favorites(user_id)
    if not favorites:
        await query.message.reply_text("Сначала добавь рецепты в избранное.")
        return

    selected = favorites[:7]
    random.shuffle(selected)

    days = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
    lines = ["<b>📅 План питания на неделю</b>\n"]
    all_ings = {}
    for i, fav in enumerate(selected):
        day = days[i] if i < 7 else f"День {i+1}"
        lines.append(f"<b>{day}:</b> {escape(fav['recipe_name'])}")
        ings_text = fav.get("ingredients", "")
        for line in ings_text.split("\n"):
            line = line.strip()
            if line:
                name = line.split(" – ")[0].strip().lower() if " – " in line else line.lower()
                if name:
                    all_ings[name] = line

    lines.append("\n<b>🛒 Список покупок на неделю:</b>")
    for ing in sorted(all_ings.values()):
        lines.append(f"• {escape(ing)}")

    text = "\n".join(lines)
    for part in split_message(text):
        await query.message.reply_text(part, parse_mode="HTML")
