import sys
import hashlib
import logging
import os
import re
from html import escape

import httpx
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes

from database import init_db, add_favorite, remove_favorite, get_favorites, is_favorite, update_rating, get_rating

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
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
    name = meal.get("strMeal") or "Unknown"
    category = meal.get("strCategory") or ""
    area = meal.get("strArea") or ""
    instructions = meal.get("strInstructions") or ""
    image = meal.get("strMealThumb") or ""
    youtube = meal.get("strYoutube") or ""

    parts = [f"<b>{escape(name)}</b>"]
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

    instr_text = escape(instructions[:500]) if instructions else ""
    if instructions and len(instructions) > 500:
        instr_text += "..."
    if instr_text.strip():
        parts.append(f"<b>Приготовление:</b>\n{instr_text}")

    if youtube:
        parts.append(f"▶ <a href='{escape(youtube)}'>Смотреть видео</a>")

    fav = is_favorite(user_id, recipe_id)
    if fav:
        rating = get_rating(user_id, recipe_id)
        parts.append(f"⭐ Ваша оценка: {'⭐' * rating}{'☆' * (5 - rating)}" if rating else "⭐ Ваша оценка: —")

    text = "\n\n".join(parts)
    if len(text) > 950 and image:
        text = text[:950] + "..."

    keyboard = [[InlineKeyboardButton("← Назад к поиску", callback_data="back_search")]]
    if fav:
        rate_row = [InlineKeyboardButton("⭐" * s + "☆" * (5 - s), callback_data=f"rate_{recipe_id}_{s}") for s in range(1, 6)]
        keyboard.insert(0, rate_row)
        keyboard.insert(1, [InlineKeyboardButton("❌ Удалить из избранного", callback_data=f"fav_del_{recipe_id}")])
    else:
        keyboard.insert(0, [InlineKeyboardButton("❤️ Добавить в избранное", callback_data=f"fav_add_{recipe_id}")])

    try:
        if image:
            msg = await query.message.reply_photo(photo=image, caption=text[:1024], parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))
            logger.info("Recipe %s photo sent, msg_id=%s", recipe_id, msg.message_id)
            await query.message.delete()
        else:
            await query.edit_message_text(text[:4000], parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))
    except Exception as e:
        logger.error("Failed to show recipe: %s", e)
        try:
            await query.edit_message_text("Не удалось показать рецепт.")
        except Exception:
            pass


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

    rate_row = [
        InlineKeyboardButton(
            "⭐" * s + "☆" * (5 - s),
            callback_data=f"rate_{recipe_id}_{s}",
        ) for s in range(1, 6)
    ]
    await query.edit_message_reply_markup(
        reply_markup=InlineKeyboardMarkup([
            rate_row,
            [InlineKeyboardButton("❌ Удалить из избранного", callback_data=f"fav_del_{recipe_id}")],
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


async def rate_recipe(update: Update, _context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    parts = query.data.split("_")
    recipe_id = parts[1]
    rating = int(parts[2])
    user_id = query.effective_user.id
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
    except Exception as e:
        logger.error("Failed to update rating: %s", e)


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

    rating = get_rating(user_id, recipe_id)
    stars_str = "⭐" * rating + "☆" * (5 - rating) if rating else "—"
    text += f"\n\n⭐ Ваша оценка: {stars_str}"

    instr = f"\n\n<b>Приготовление:</b>\n{escape(meal['instructions'][:500])}"
    if len(meal["instructions"]) > 500:
        instr += "..."

    youtube = meal.get("youtube_url", "")
    if youtube:
        instr += f"\n\n▶ <a href='{escape(youtube)}'>Смотреть видео</a>"

    free = limit - len(text)
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
        r = int(fav.get("rating", 0) or 0)
        stars = " " + "⭐" * r if r else ""
        keyboard.append([InlineKeyboardButton(f"{fav['recipe_name']}{stars}", callback_data=f"fav_view_{fav['recipe_id']}")])

    await query.edit_message_text(
        f"📚 Твои избранные рецепты ({len(favorites)}):",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    logger.info("Callback: %s", data)

    if data.startswith("recipe_"):
        await show_recipe(update, context)
    elif data.startswith("fav_add_"):
        await add_favorite_handler(update, context)
    elif data.startswith("fav_del_"):
        await remove_favorite_handler(update, context)
    elif data.startswith("fav_view_"):
        await view_favorite(update, context)
    elif data.startswith("rate_"):
        await rate_recipe(update, context)
    elif data == "back_search":
        await back_to_search(update, context)
    elif data == "back_fav":
        await back_to_favorites(update, context)
    else:
        await query.answer()
        await query.edit_message_text(f"Неизвестная команда: {data}. /start")


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
    app.add_handler(CallbackQueryHandler(callback_router))

    port = int(os.getenv("PORT", "10000"))
    render_url = os.getenv("RENDER_EXTERNAL_URL", f"https://food-recipe-bot.onrender.com")
    webhook_path = os.getenv("WEBHOOK_PATH", "/webhook")
    webhook_url = f"{render_url}{webhook_path}"
    import hashlib
    secret = os.getenv("WEBHOOK_SECRET", hashlib.sha256(token.encode()).hexdigest()[:32])

    logger.info("Starting webhook on port %d at %s", port, webhook_url)
    app.run_webhook(
        listen="0.0.0.0",
        port=port,
        url_path=webhook_path,
        webhook_url=webhook_url,
        secret_token=secret,
        allowed_updates=Update.ALL_TYPES,
    )


if __name__ == "__main__":
    main()
