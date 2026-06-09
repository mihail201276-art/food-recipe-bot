import logging
import os

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from database import get_profile, save_profile
from utils.keyboards import MAIN_KEYBOARD

logger = logging.getLogger(__name__)


async def start(update: Update, _context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    logger.info("User %s started the bot", user.id)
    await update.message.reply_text(
        f"Привет, {user.first_name}! Я помогу тебе найти рецепты и сохранить их в избранное.\n\n"
        "Ещё есть @Smart_pomogator_bot — спроси про замены ингредиентов, диеты, "
        "что приготовить из того, что есть в холодильнике.\n\n"
        "/settings — рассказать об аллергиях и диете",
        reply_markup=MAIN_KEYBOARD,
    )


async def help_command(update: Update, _context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🔍 <b>Поиск рецептов</b> — напиши название блюда на любом языке\n"
        "📚 <b>Мои рецепты</b> — избранное, оценки, список покупок, план на неделю\n"
        "🍳 <b>Что приготовить</b> — напиши продукты, ИИ предложит блюда\n"
        "🎲 <b>Удиви меня</b> — случайный рецепт\n"
        "🔍 <b>Фильтры</b> — поиск по категории, кухне, ингредиенту\n"
        "📸 <b>Фото продуктов</b> — сфоткай холодильник, ИИ скажет что приготовить\n"
        "🎤 <b>Голосовые сообщения</b> — продиктуй запрос\n"
        "/settings — профиль (аллергии, диета)\n"
        "/premium — ⭐ премиум-доступ\n"
        "/donate — ☕ поддержать проект\n\n"
        "Есть ещё @Smart_pomogator_bot — кулинарный помощник.",
        parse_mode="HTML",
    )


async def donate_cmd(update: Update, _context: ContextTypes.DEFAULT_TYPE):
    url = os.getenv("DONATION_URL", "")
    text = (
        "<b>☕ Поддержать проект</b>\n\n"
        "Если бот помогает тебе на кухне, можешь поддержать автора:\n"
        f"{'🔗 ' + url if url else 'Свяжись с @mihail201276'}"
    )
    await update.message.reply_text(text, parse_mode="HTML")


async def back_to_main(update: Update, _context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.message.delete()
    await query.message.reply_text("Главное меню:", reply_markup=MAIN_KEYBOARD)


async def back_to_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.message.delete()
    await context.bot.send_message(
        chat_id=query.message.chat_id,
        text="Напиши название блюда для поиска:",
    )


async def back_to_favorites(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    from database import get_favorites

    favorites = get_favorites(user_id)
    if not favorites:
        await query.message.delete()
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text="У тебя пока нет избранных рецептов.",
        )
        return

    keyboard = []
    for fav in favorites:
        r = int(fav.get("rating", 0) or 0)
        stars = " " + "⭐" * r if r else ""
        keyboard.append([InlineKeyboardButton(f"{fav['recipe_name']}{stars}", callback_data=f"fav_view_{fav['recipe_id']}")])

    keyboard.append([InlineKeyboardButton("🛒 Список покупок", callback_data="shopping_list")])
    keyboard.append([InlineKeyboardButton("📅 План на неделю", callback_data="meal_plan")])
    keyboard.append([InlineKeyboardButton("🏠 Главное меню", callback_data="back_main")])

    await query.message.delete()
    await context.bot.send_message(
        chat_id=query.message.chat_id,
        text=f"📚 Твои избранные рецепты ({len(favorites)}):",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
