import logging
import os
from html import escape

import httpx
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes

from database import init_db, add_favorite, remove_favorite, get_favorites, is_favorite

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler("bot.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)

MEALDB_BASE = "https://www.themealdb.com/api/json/v1/1"
MAX_RESULTS = 8

MAIN_KEYBOARD = ReplyKeyboardMarkup(
    [["🔍 Поиск рецептов", "📚 Мои рецепты"]],
    resize_keyboard=True,
)


async def start(update: Update, _context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    logger.info("User %s started the bot", user.id)
    await update.message.reply_text(
        f"Привет, {user.first_name}! Я помогу тебе найти рецепты и сохранить их в избранное.",
        reply_markup=MAIN_KEYBOARD,
    )


async def search_prompt(update: Update, _context: ContextTypes.DEFAULT_TYPE):
    logger.info("User %s requested search", update.effective_user.id)
    await update.message.reply_text("Напиши название блюда (на английском, например Chicken, Pasta):")


async def search_recipes(update: Update, _context: ContextTypes.DEFAULT_TYPE):
    query = update.message.text.strip()
    user_id = update.effective_user.id
    logger.info("User %s searching for: %s", user_id, query)

    if not query:
        await update.message.reply_text("Пожалуйста, введи название блюда.")
        return

    await update.message.reply_chat_action("typing")

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{MEALDB_BASE}/search.php", params={"s": query}, timeout=15)
            resp.raise_for_status()
            data = resp.json()
    except Exception as e:
        logger.error("API request failed: %s", e)
        await update.message.reply_text("Ошибка при поиске. Попробуй позже.")
        return

    meals = data.get("meals", [])
    if not meals:
        logger.info("No results for query: %s", query)
        await update.message.reply_text("Ничего не найдено. Попробуй другое название.")
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


async def show_recipe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    recipe_id = query.data.replace("recipe_", "")
    user_id = query.effective_user.id
    logger.info("User %s viewing recipe %s", user_id, recipe_id)

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{MEALDB_BASE}/lookup.php", params={"i": recipe_id}, timeout=15)
            resp.raise_for_status()
            data = resp.json()
    except Exception as e:
        logger.error("Failed to fetch recipe %s: %s", recipe_id, e)
        await query.edit_message_text("Ошибка загрузки рецепта.")
        return

    meals = data.get("meals", [])
    if not meals:
        await query.edit_message_text("Рецепт не найден.")
        return

    meal = meals[0]
    name = meal.get("strMeal", "Unknown")
    category = meal.get("strCategory", "")
    area = meal.get("strArea", "")
    instructions = meal.get("strInstructions", "")
    image = meal.get("strMealThumb", "")
    youtube = meal.get("strYoutube", "")

    ingredients = []
    for i in range(1, 21):
        ing = meal.get(f"strIngredient{i}")
        meas = meal.get(f"strMeasure{i}")
        if ing and ing.strip():
            ingredients.append(f"• {ing.strip()} — {meas.strip()}" if meas else f"• {ing.strip()}")

    text = f"<b>{escape(name)}</b>\n"
    if category:
        text += f"🏷 Категория: {escape(category)}\n"
    if area:
        text += f"🌍 Кухня: {escape(area)}\n"
    text += f"\n<b>Ингредиенты:</b>\n" + "\n".join(ingredients)

    has_image = bool(image)
    limit = 950 if has_image else 3900
    if len(text) > limit:
        text = text[:limit] + "..."

    instr = f"\n\n<b>Приготовление:</b>\n{escape(instructions[:600])}"
    if len(instructions) > 600:
        instr += "..."
    if youtube:
        instr += f"\n\n▶ <a href='{escape(youtube)}'>YouTube</a>"

    free = limit - len(text)
    if free > 60:
        text += instr[:free]

    fav = is_favorite(user_id, recipe_id)
    fav_text = "❌ Удалить из избранного" if fav else "❤️ Добавить в избранное"
    fav_cb = f"fav_del_{recipe_id}" if fav else f"fav_add_{recipe_id}"

    keyboard = [
        [InlineKeyboardButton(fav_text, callback_data=fav_cb)],
        [InlineKeyboardButton("← Назад к поиску", callback_data="back_search")],
    ]

    try:
        if has_image:
            await query.message.reply_photo(
                photo=image, caption=text, parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
            await query.message.delete()
        else:
            await query.edit_message_text(
                text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard)
            )
    except Exception as e:
        logger.error("Failed to show recipe %s: %s", recipe_id, e)
        await query.edit_message_text("Ошибка при загрузке рецепта.")


async def add_favorite_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    recipe_id = query.data.replace("fav_add_", "")
    user_id = query.effective_user.id
    logger.info("User %s adding favorite recipe %s", user_id, recipe_id)

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{MEALDB_BASE}/lookup.php", params={"i": recipe_id}, timeout=15)
            resp.raise_for_status()
            data = resp.json()
    except Exception as e:
        logger.error("Failed to fetch recipe %s: %s", recipe_id, e)
        await query.edit_message_text("Ошибка.")
        return

    meals = data.get("meals", [])
    if not meals:
        return

    ok = add_favorite(user_id, meals[0])
    if ok:
        await query.answer("✅ Добавлено в избранное!", show_alert=True)
    else:
        await query.answer("Уже в избранном.", show_alert=True)

    fav_btn = InlineKeyboardButton("❌ Удалить из избранного", callback_data=f"fav_del_{recipe_id}")
    await query.edit_message_reply_markup(
        reply_markup=InlineKeyboardMarkup([
            [fav_btn],
            [InlineKeyboardButton("← Назад к поиску", callback_data="back_search")],
        ])
    )


async def remove_favorite_handler(update: Update, _context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    recipe_id = query.data.replace("fav_del_", "")
    user_id = query.effective_user.id
    logger.info("User %s removing favorite recipe %s", user_id, recipe_id)

    ok = remove_favorite(user_id, recipe_id)
    if ok:
        await query.answer("✅ Удалено из избранного!", show_alert=True)
    else:
        await query.answer("Не найдено.", show_alert=True)

    fav_btn = InlineKeyboardButton("❤️ Добавить в избранное", callback_data=f"fav_add_{recipe_id}")
    await query.edit_message_reply_markup(
        reply_markup=InlineKeyboardMarkup([
            [fav_btn],
            [InlineKeyboardButton("← Назад к поиску", callback_data="back_search")],
        ])
    )


async def my_favorites(update: Update, _context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    logger.info("User %s viewing favorites", user_id)

    favorites = get_favorites(user_id)
    if not favorites:
        await update.message.reply_text("У тебя пока нет избранных рецептов.")
        return

    keyboard = []
    for fav in favorites:
        keyboard.append([InlineKeyboardButton(fav["recipe_name"], callback_data=f"fav_view_{fav['recipe_id']}")])

    await update.message.reply_text(
        f"📚 Твои избранные рецепты ({len(favorites)}):",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def view_favorite(update: Update, _context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    recipe_id = query.data.replace("fav_view_", "")
    user_id = query.effective_user.id

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
    limit = 950 if has_image else 3900
    if len(text) > limit:
        text = text[:limit] + "..."

    instr = f"\n\n<b>Приготовление:</b>\n{escape(meal['instructions'][:600])}"
    if len(meal["instructions"]) > 600:
        instr += "..."

    free = limit - len(text)
    if free > 60:
        text += instr[:free]

    keyboard = [
        [InlineKeyboardButton("❌ Удалить из избранного", callback_data=f"fav_del_{recipe_id}")],
        [InlineKeyboardButton("← Назад к избранному", callback_data="back_fav")],
    ]

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
    except Exception as e:
        logger.error("Failed to view favorite %s: %s", recipe_id, e)
        await query.edit_message_text("Ошибка при загрузке рецепта.")


async def back_to_search(update: Update, _context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "Напиши название блюда для поиска (на английском):"
    )


async def back_to_favorites(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.effective_user.id

    favorites = get_favorites(user_id)
    if not favorites:
        await query.edit_message_text("У тебя пока нет избранных рецептов.")
        return

    keyboard = []
    for fav in favorites:
        keyboard.append([InlineKeyboardButton(fav["recipe_name"], callback_data=f"fav_view_{fav['recipe_id']}")])

    await query.edit_message_text(
        f"📚 Твои избранные рецепты ({len(favorites)}):",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    user_id = update.effective_user.id

    if text == "🔍 Поиск рецептов":
        await search_prompt(update, context)
    elif text == "📚 Мои рецепты":
        await my_favorites(update, context)
    else:
        await search_recipes(update, context)


def main():
    token = os.getenv("BOT_TOKEN")
    if not token:
        logger.error("BOT_TOKEN environment variable not set!")
        return

    init_db()
    logger.info("Starting bot...")

    app = Application.builder().token(token).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(CallbackQueryHandler(show_recipe, pattern=r"^recipe_\d+$"))
    app.add_handler(CallbackQueryHandler(add_favorite_handler, pattern=r"^fav_add_\d+$"))
    app.add_handler(CallbackQueryHandler(remove_favorite_handler, pattern=r"^fav_del_\d+$"))
    app.add_handler(CallbackQueryHandler(view_favorite, pattern=r"^fav_view_\d+$"))
    app.add_handler(CallbackQueryHandler(back_to_search, pattern=r"^back_search$"))
    app.add_handler(CallbackQueryHandler(back_to_favorites, pattern=r"^back_fav$"))

    logger.info("Bot is running. Press Ctrl+C to stop.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
