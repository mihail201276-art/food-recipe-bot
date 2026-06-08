import sys
import os
import re
import asyncio
import base64
import io
import threading
from html import escape

from dotenv import load_dotenv

load_dotenv()

import httpx
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, LabeledPrice
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, PreCheckoutQueryHandler, filters, ContextTypes

from database import init_db, add_favorite, remove_favorite, get_favorites, is_favorite, update_rating, get_rating, get_translation, save_translation, get_profile, save_profile, set_premium, check_translation_limit, increment_translation_usage
from assistant_bot import run_assistant_bot
import llm
from llm import split_message, transcribe_audio, _call_proxyapi_vision

import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

MEALDB_BASE = "https://www.themealdb.com/api/json/v1/1"
COCKTAILDB_BASE = "https://www.thecocktaildb.com/api/json/v1/1"
MAX_RESULTS = int(os.getenv("MAX_RESULTS", "8"))
TRANSLATE_DAILY_LIMIT = int(os.getenv("TRANSLATE_DAILY_LIMIT", "20"))
PREMIUM_DAILY_LIMIT = int(os.getenv("PREMIUM_DAILY_LIMIT", "100"))

MAIN_KEYBOARD = ReplyKeyboardMarkup(
    [["🔍 Поиск рецептов", "📚 Мои рецепты"],
     ["🍳 Что приготовить", "🎲 Удиви меня"],
     ["🍸 Коктейли", "🔍 Фильтры"],
     ["❓ Помощь", "🔄 Перезапустить"]],
    resize_keyboard=True,
)

COCKTAIL_KEYBOARD = ReplyKeyboardMarkup(
    [["🔍 Поиск коктейлей", "🎲 Случайный коктейль"],
     ["🍹 Алкогольные", "🧃 Безалкогольные"],
     ["← На главную"]],
    resize_keyboard=True,
)


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


async def search_prompt(update: Update, _context: ContextTypes.DEFAULT_TYPE):
    logger.info("User %s requested search", update.effective_user.id)
    await update.message.reply_text("Напиши название блюда (например, борщ, паста с курицей):")


async def search_recipes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = (context.user_data.pop("voice_text", None) or update.message.text).strip()
    user_id = update.effective_user.id
    logger.info("User %s searching for: %s", user_id, query)

    if not query:
        await update.message.reply_text("Пожалуйста, введи название блюда.")
        return

    await update.message.reply_chat_action("typing")

    # если есть кириллица — переводим через LLM
    if re.search(r'[а-яА-ЯёЁ]', query):
        logger.info("Non-Latin query detected, translating: %s", query)
        system = "Переведи название блюда на английский. Ответь только одним-двумя словами, без пояснений."
        translated = await asyncio.to_thread(llm.get_llm_response, query, system)
        if translated and not translated.startswith("⚠"):
            logger.info("Translated '%s' -> '%s'", query, translated)
            query = translated.strip().lower()

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
        logger.info("No results for query, generating via AI: %s", query)
        await update.message.reply_text("🧠 В TheMealDB ничего нет, придумываю рецепт через ИИ...")
        recipe = await asyncio.to_thread(llm.generate_recipe, query)
        if recipe and not recipe.startswith("⚠"):
            keyboard = [[InlineKeyboardButton("🔍 Новый поиск", callback_data="back_search"),
                          InlineKeyboardButton("🏠 Главное меню", callback_data="back_main")]]
            await update.message.reply_text(
                recipe, parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
        else:
            await update.message.reply_text("Не удалось сгенерировать рецепт. Попробуй другое название.")
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


# ───── Коктейли ─────

async def cocktail_menu(update: Update, _context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🍸 Выбери действие:", reply_markup=COCKTAIL_KEYBOARD)


async def search_cocktails(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = (context.user_data.pop("voice_text", None) or update.message.text).strip().lower()
    logger.info("Searching cocktails: %s", query)
    await update.message.reply_chat_action("typing")
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{COCKTAILDB_BASE}/search.php", params={"s": query}, timeout=15)
            resp.raise_for_status()
            data = resp.json()
    except Exception as e:
        logger.error("Cocktail search failed: %s", e)
        await update.message.reply_text("Ошибка поиска.")
        return
    drinks = data.get("drinks", [])
    if not drinks:
        await update.message.reply_text("Ничего не найдено. Попробуй другое название.")
        return
    drinks = drinks[:MAX_RESULTS]
    keyboard = [[InlineKeyboardButton(d["strDrink"], callback_data=f"drink_{d['idDrink']}")] for d in drinks]
    await update.message.reply_text(
        f"Найдено коктейлей: {len(drinks)}",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def show_cocktail(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    drink_id = query.data.replace("drink_", "")
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{COCKTAILDB_BASE}/lookup.php", params={"i": drink_id}, timeout=15)
            resp.raise_for_status()
            data = resp.json()
    except Exception as e:
        logger.error("Failed to fetch cocktail %s: %s", drink_id, e)
        await query.edit_message_text("Ошибка.")
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
    image = d.get("strDrinkThumb") or ""

    await query.message.delete()
    if image:
        await context.bot.send_photo(chat_id=query.message.chat_id, photo=image, caption=f"<b>{escape(name)}</b>", parse_mode="HTML")

    parts = []
    if category:
        parts.append(f"🏷 {escape(category)}")
    if alcoholic:
        parts.append(f"🍸 {escape(alcoholic)}")
    if glass:
        parts.append(f"🥃 Бокал: {escape(glass)}")

    ings = []
    for i in range(1, 16):
        ing = d.get(f"strIngredient{i}")
        meas = d.get(f"strMeasure{i}")
        if ing and ing.strip():
            ings.append(f"• {escape(ing.strip())}" + (f" — {escape(meas.strip())}" if meas else ""))
    if ings:
        parts.append("<b>Ингредиенты:</b>\n" + "\n".join(ings))

    if instructions:
        parts.append(f"<b>Приготовление:</b>\n{escape(instructions)}")

    text = "\n\n".join(parts)
    keyboard = [[InlineKeyboardButton("🌐 Перевести на русский", callback_data=f"transdrink_{drink_id}")],
                 [InlineKeyboardButton("🎲 Случайный коктейль", callback_data="random_drink"),
                  InlineKeyboardButton("🏠 Главное меню", callback_data="back_main")],
                 [InlineKeyboardButton("← К коктейлям", callback_data="back_cocktail")]]
    chunks = split_message(text, 4000)
    for i, chunk in enumerate(chunks):
        is_last = (i == len(chunks) - 1)
        await context.bot.send_message(
            chat_id=query.message.chat_id, text=chunk, parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(keyboard) if is_last else None,
        )


async def random_cocktail_handler(update: Update, _context: ContextTypes.DEFAULT_TYPE):
    target = update.callback_query.message if update.callback_query else update.message
    if update.callback_query:
        await update.callback_query.answer()
    await target.reply_chat_action("typing")
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{COCKTAILDB_BASE}/random.php", timeout=15)
            resp.raise_for_status()
            data = resp.json()
    except Exception as e:
        logger.error("Random cocktail failed: %s", e)
        await target.reply_text("Ошибка.")
        return
    drinks = data.get("drinks", [])
    if not drinks:
        await target.reply_text("Не удалось.")
        return
    drink_id = drinks[0]["idDrink"]
    keyboard = [[InlineKeyboardButton("🍸 Показать коктейль", callback_data=f"drink_{drink_id}")]]
    await target.reply_text(f"🍸 <b>{escape(drinks[0]['strDrink'])}</b>", parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))


async def filter_cocktails(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    params = {}
    if data == "cocktail_alcoholic":
        params = {"a": "Alcoholic"}
    elif data == "cocktail_non_alcoholic":
        params = {"a": "Non_Alcoholic"}
    else:
        return
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{COCKTAILDB_BASE}/filter.php", params=params, timeout=15)
            resp.raise_for_status()
            data_json = resp.json()
    except Exception as e:
        logger.error("Cocktail filter failed: %s", e)
        await query.message.reply_text("Ошибка.")
        return
    drinks = data_json.get("drinks", [])
    if not drinks:
        await query.message.reply_text("Ничего не найдено.")
        return
    drinks = drinks[:MAX_RESULTS]
    keyboard = [[InlineKeyboardButton(d["strDrink"], callback_data=f"drink_{d['idDrink']}")] for d in drinks]
    await query.message.reply_text(
        f"Найдено коктейлей: {len(drinks)}",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def filter_cocktails_by_alcohol(update: Update, context: ContextTypes.DEFAULT_TYPE, alcoholic: str):
    await update.message.reply_chat_action("typing")
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{COCKTAILDB_BASE}/filter.php", params={"a": alcoholic}, timeout=15)
            resp.raise_for_status()
            data = resp.json()
    except Exception as e:
        logger.error("Cocktail filter failed: %s", e)
        await update.message.reply_text("Ошибка.")
        return
    drinks = data.get("drinks", [])
    if not drinks:
        await update.message.reply_text("Ничего не найдено.")
        return
    drinks = drinks[:MAX_RESULTS]
    keyboard = [[InlineKeyboardButton(d["strDrink"], callback_data=f"drink_{d['idDrink']}")] for d in drinks]
    await update.message.reply_text(
        f"Найдено коктейлей: {len(drinks)}",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def back_to_cocktail(update: Update, _context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.message.delete()
    await query.message.reply_text("🍸 Выбери действие:", reply_markup=COCKTAIL_KEYBOARD)


async def show_recipe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    recipe_id = query.data.replace("recipe_", "")
    user_id = query.from_user.id
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

    # фото отдельно — завлекает
    await query.message.delete()
    if image:
        await context.bot.send_photo(chat_id=query.message.chat_id, photo=image, caption=f"<b>{escape(name)}</b>", parse_mode="HTML")

    # полный рецепт текстом, ничего не обрезаем
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
    keyboard.append([InlineKeyboardButton("🌐 Перевести на русский", callback_data=f"translate_{recipe_id}")])
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


async def add_favorite_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    recipe_id = query.data.replace("fav_add_", "")
    user_id = query.from_user.id
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

    ok = await asyncio.to_thread(add_favorite, user_id, meals[0])
    if ok:
        await query.answer("✅ Добавлено в избранное!", show_alert=True)
    else:
        await query.answer("Уже в избранном.", show_alert=True)

    instr = meals[0].get("strInstructions") or ""
    rate_row = [
        InlineKeyboardButton(
            "⭐" * s + "☆" * (5 - s),
            callback_data=f"rate_{recipe_id}_{s}",
        ) for s in range(1, 6)
    ]
    add_btns = [[InlineKeyboardButton("❌ Удалить из избранного", callback_data=f"fav_del_{recipe_id}")]]
    if instr and len(instr) > 500:
        add_btns.append([InlineKeyboardButton("📖 Полный рецепт", callback_data=f"full_recipe_{recipe_id}")])
    add_btns.append([InlineKeyboardButton("🌐 Перевести на русский", callback_data=f"translate_{recipe_id}")])
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
    # check if instructions were long enough to show full recipe button
    instr = ""
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{MEALDB_BASE}/lookup.php", params={"i": recipe_id}, timeout=15)
            resp.raise_for_status()
            data = resp.json()
            if data.get("meals"):
                instr = data["meals"][0].get("strInstructions") or ""
    except Exception:
        pass
    if instr and len(instr) > 500:
        add_btns.append([InlineKeyboardButton("📖 Полный рецепт", callback_data=f"full_recipe_{recipe_id}")])
    add_btns.append([InlineKeyboardButton("🌐 Перевести на русский", callback_data=f"translate_{recipe_id}")])
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
    except Exception as e:
        logger.error("Failed to view favorite %s: %s", recipe_id, e)
        await query.edit_message_text("Ошибка при загрузке рецепта.")


async def translate_recipe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    recipe_id = query.data.replace("translate_", "")
    user_id = query.from_user.id
    logger.info("User %s translating recipe %s", user_id, recipe_id)

    profile = get_profile(user_id)
    limit = PREMIUM_DAILY_LIMIT if profile.get("premium") else TRANSLATE_DAILY_LIMIT
    if not check_translation_limit(user_id, limit):
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
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{MEALDB_BASE}/lookup.php", params={"i": recipe_id}, timeout=15)
            resp.raise_for_status()
            data = resp.json()
    except Exception as e:
        logger.error("Failed to fetch recipe %s: %s", recipe_id, e)
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
    reply = await asyncio.to_thread(llm.get_llm_response, recipe_text, system_prompt)
    increment_translation_usage(user_id)
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
    if not check_translation_limit(user_id, limit):
        await query.message.reply_text(f"⚠️ Лимит переводов на сегодня ({limit} шт.) исчерпан.")
        return

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{COCKTAILDB_BASE}/lookup.php", params={"i": drink_id}, timeout=15)
            resp.raise_for_status()
            data = resp.json()
    except Exception as e:
        logger.error("Failed to fetch cocktail %s: %s", drink_id, e)
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
    reply = await asyncio.to_thread(llm.get_llm_response, drink_text, system)
    increment_translation_usage(user_id)

    text = f"<b>🌐 Перевод на русский:</b>\n\n{reply}"
    for chunk in split_message(text, 4000):
        await query.message.reply_text(chunk, parse_mode="HTML")


async def full_recipe_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    recipe_id = query.data.replace("full_recipe_", "")
    logger.info("Full recipe requested for %s", recipe_id)

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{MEALDB_BASE}/lookup.php", params={"i": recipe_id}, timeout=15)
            resp.raise_for_status()
            data = resp.json()
    except Exception as e:
        logger.error("Failed to fetch recipe %s: %s", recipe_id, e)
        await query.message.reply_text("Ошибка загрузки рецепта.")
        return

    meals = data.get("meals", [])
    if not meals:
        await query.message.reply_text("Рецепт не найден.")
        return

    instructions = meals[0].get("strInstructions") or ""
    name = meals[0].get("strMeal") or "Рецепт"
    text = f"<b>{escape(name)}</b>\n\n<b>Приготовление:</b>\n{escape(instructions)}"

    # разбиваем на части, если длиннее 4000 символов
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
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{MEALDB_BASE}/random.php", timeout=15)
            resp.raise_for_status()
            data = resp.json()
    except Exception as e:
        logger.error("Random recipe failed: %s", e)
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
    data = query.data  # flist_cat, flist_cuisine, flist_ing

    filter_type = data.replace("flist_", "")
    param_key = {"cat": "c", "cuisine": "a", "ing": "i"}.get(filter_type)
    if not param_key:
        return

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{MEALDB_BASE}/list.php", params={param_key: "list"}, timeout=15)
            resp.raise_for_status()
            data = resp.json()
    except Exception as e:
        logger.error("Failed to fetch filter list: %s", e)
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
    data = query.data  # fcat_Seafood, fcuisine_Italian, fing_chicken

    prefix_map = {"fcat_": ("c", "strCategory"), "fcuisine_": ("a", "strArea"), "fing_": ("i", "strIngredient")}
    param_key = None
    for p, (pk, _) in prefix_map.items():
        if data.startswith(p):
            param_key = pk
            value = data[len(p):]
            break
    if not param_key:
        return

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{MEALDB_BASE}/filter.php", params={param_key: value}, timeout=15)
            resp.raise_for_status()
            data = resp.json()
    except Exception as e:
        logger.error("Filter failed: %s", e)
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

    system = (
        "У тебя есть следующие продукты. Предложи 3-5 блюд, которые можно из них приготовить. "
        "Каждое блюдо напиши с новой строки, начиная с «•». "
        "После названия в скобках укажи ключевые слова для поиска на английском. "
        "Пример: • Куриный суп с рисом (chicken rice soup)\nОтвет дай только на русском."
    )
    reply = await asyncio.to_thread(llm.get_llm_response, text, system)
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
                # extract ingredient name (before the dash)
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


async def adapt_recipe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data  # adapt_lactose_52772, adapt_simple_52772, adapt_portion_52772
    parts = data.split("_", 2)
    adapt_type = parts[1]
    recipe_id = parts[2]
    user_id = query.from_user.id
    logger.info("User %s adapting recipe %s: %s", user_id, recipe_id, adapt_type)

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{MEALDB_BASE}/lookup.php", params={"i": recipe_id}, timeout=15)
            resp.raise_for_status()
            data = resp.json()
    except Exception as e:
        logger.error("Failed to fetch recipe %s: %s", recipe_id, e)
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
    reply = await asyncio.to_thread(llm.get_llm_response, recipe_text, system)
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


async def settings_cmd(update: Update, _context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    profile = get_profile(user_id)
    badge = "⭐ Премиум" if profile.get("premium") else "—"
    text = (
        f"<b>⚙️ Настройки профиля</b>\n\n"
        f"🥜 Аллергии: {profile.get('allergies') or 'не указано'}\n"
        f"🥗 Диета: {profile.get('diet') or 'не указано'}\n"
        f"🌾 Без глютена: {'да' if profile.get('gluten_free') else 'нет'}\n"
        f"💎 Статус: {badge}\n"
        f"📊 Лимит переводов: {PREMIUM_DAILY_LIMIT if profile.get('premium') else TRANSLATE_DAILY_LIMIT}/день\n\n"
        "Выбери, что хочешь изменить:"
    )
    keyboard = [
        [InlineKeyboardButton("🥜 Аллергии", callback_data="prof_allergies")],
        [InlineKeyboardButton("🥗 Диета (веган, кето...)", callback_data="prof_diet")],
        [InlineKeyboardButton("🌾 Без глютена", callback_data="prof_gluten")],
        [InlineKeyboardButton("❌ Сбросить", callback_data="prof_reset")],
    ]
    await update.message.reply_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))


async def settings_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = query.from_user.id

    if data == "prof_allergies":
        context.user_data["prof_field"] = "allergies"
        await query.edit_message_text("Напиши свои аллергии через запятую (например: орехи, молоко, яйца):")
    elif data == "prof_diet":
        context.user_data["prof_field"] = "diet"
        await query.edit_message_text("Напиши тип диеты (например: веган, кето, без сахара):")
    elif data == "prof_gluten":
        profile = get_profile(user_id)
        val = 0 if profile.get("gluten_free") else 1
        save_profile(user_id, "gluten_free", val)
        await query.edit_message_text("✅ Настройки обновлены.")
        context.user_data.pop("prof_field", None)
    elif data == "prof_reset":
        save_profile(user_id, "allergies", "")
        save_profile(user_id, "diet", "")
        save_profile(user_id, "gluten_free", 0)
        await query.edit_message_text("✅ Профиль сброшен.")
        context.user_data.pop("prof_field", None)


async def premium_cmd(update: Update, _context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    profile = get_profile(user_id)
    if profile.get("premium"):
        await update.message.reply_text("⭐ У тебя уже есть премиум! Спасибо за поддержку.", parse_mode="HTML")
        return
    text = (
        "<b>⭐ Премиум-доступ</b>\n\n"
        "<b>Преимущества:</b>\n"
        f"• {PREMIUM_DAILY_LIMIT} переводов рецептов в день (вместо {TRANSLATE_DAILY_LIMIT})\n"
        "• Приоритетная обработка фото холодильника\n"
        "• Доступ ко всем адаптациям рецептов\n"
        "• Скоро: эксклюзивные функции\n\n"
        "<b>Цена:</b> 50 ⭐ Telegram Stars — разовая оплата навсегда"
    )
    keyboard = [[InlineKeyboardButton("⭐ Купить премиум за 50 Stars", callback_data="premium_buy")]]
    await update.message.reply_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))


async def premium_buy_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    profile = get_profile(user_id)
    if profile.get("premium"):
        await query.edit_message_text("⭐ У тебя уже есть премиум!", parse_mode="HTML")
        return

    await context.bot.send_invoice(
        chat_id=user_id,
        title="⭐ Премиум-доступ",
        description=f"Премиум в Food Recipe Bot: {PREMIUM_DAILY_LIMIT} переводов/день и все функции",
        payload=f"premium_{user_id}",
        provider_token="",
        currency="XTR",
        prices=[LabeledPrice("⭐ Премиум навсегда", 50)],
    )
    await query.edit_message_text("💳 Отправлен счёт на оплату. Подтверди в Telegram.", parse_mode="HTML")


async def pre_checkout_handler(update: Update, _context: ContextTypes.DEFAULT_TYPE):
    query = update.pre_checkout_query
    await query.answer(ok=True)


async def handle_successful_payment(update: Update, _context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    payload = update.message.successful_payment.invoice_payload
    logger.info("Successful payment from user %s: %s", user_id, payload)
    set_premium(user_id, 1)
    await update.message.reply_text(
        "⭐ <b>Премиум активирован!</b>\n\n"
        f"Тебе доступно {PREMIUM_DAILY_LIMIT} переводов в день. Спасибо за поддержку!",
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


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    logger.info("User %s sent voice message", user_id)

    file = await update.message.voice.get_file()
    file_path = f"/tmp/voice_{user_id}.ogg"
    await file.download_to_drive(file_path)

    await update.message.reply_chat_action("typing")
    text = await asyncio.to_thread(transcribe_audio, file_path)
    if not text:
        await update.message.reply_text("Не удалось распознать голос.")
        return

    logger.info("Transcribed: %s", text)
    context.user_data["state"] = None
    context.user_data["voice_text"] = text.strip()
    await search_recipes(update, context)


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    logger.info("User %s sent photo", user_id)

    await update.message.reply_chat_action("typing")

    file = await update.message.photo[-1].get_file()
    buf = io.BytesIO()
    try:
        await file.download_to_memory(buf)
        buf.seek(0)
        b64 = base64.b64encode(buf.read()).decode()
        data_uri = f"data:image/jpeg;base64,{b64}"
    except Exception as e:
        logger.error("Failed to download photo: %s", e)
        await update.message.reply_text("Не удалось загрузить фото.")
        return

    prompt = "Посмотри на фото продуктов. Предложи 3-5 блюд, которые можно из них приготовить. Напиши на русском, кратко."
    reply = await asyncio.to_thread(_call_proxyapi_vision, data_uri, prompt)
    if not reply:
        await update.message.reply_text("Не удалось проанализировать фото.")
        return

    for part in split_message(reply):
        await update.message.reply_text(part)


async def meal_plan(update: Update, _context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    favorites = get_favorites(user_id)
    if not favorites:
        await query.message.reply_text("Сначала добавь рецепты в избранное.")
        return

    import random
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


async def back_to_main(update: Update, _context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.message.delete()
    await query.message.reply_text(
        "Главное меню:", reply_markup=MAIN_KEYBOARD
    )


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
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{MEALDB_BASE}/lookup.php", params={"i": recipe_id}, timeout=15)
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
    elif data.startswith("translate_"):
        await translate_recipe(update, context)
    elif data.startswith("full_recipe_"):
        await full_recipe_handler(update, context)
    elif data.startswith("adapt_"):
        await adapt_recipe(update, context)
    elif data == "random_":
        await random_recipe(update, context)
    elif data == "filter_menu":
        await filter_menu(update, context)
    elif data.startswith("flist_"):
        await filter_list(update, context)
    elif data.startswith("fcat_") or data.startswith("fcuisine_") or data.startswith("fing_"):
        await filter_results(update, context)
    elif data == "shopping_list":
        await shopping_list(update, context)
    elif data == "meal_plan":
        await meal_plan(update, context)
    elif data.startswith("share_"):
        await share_recipe(update, context)
    elif data.startswith("prof_"):
        await settings_callback(update, context)
    elif data == "premium_buy":
        await premium_buy_handler(update, context)
    elif data == "back_main":
        await back_to_main(update, context)
    elif data == "back_search":
        await back_to_search(update, context)
    elif data == "back_fav":
        await back_to_favorites(update, context)
    elif data.startswith("ai_variation_"):
        await ai_variation_recipe(update, context)
    elif data.startswith("transdrink_"):
        await translate_cocktail(update, context)
    elif data.startswith("drink_"):
        await show_cocktail(update, context)
    elif data == "random_drink":
        await random_cocktail_handler(update, context)
    elif data == "back_cocktail":
        await back_to_cocktail(update, context)
    elif data == "cocktail_alcoholic" or data == "cocktail_non_alcoholic":
        await filter_cocktails(update, context)
    else:
        await query.answer()
        await query.edit_message_text(f"Неизвестная команда: {data}. /start")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    user_id = update.effective_user.id

    state = context.user_data.get("state")
    prof_field = context.user_data.get("prof_field")

    if prof_field:
        save_profile(user_id, prof_field, text)
        context.user_data.pop("prof_field", None)
        await update.message.reply_text("✅ Настройки сохранены. /settings")
        return

    if text == "🔍 Поиск рецептов":
        context.user_data.pop("state", None)
        await search_prompt(update, context)
    elif text == "📚 Мои рецепты":
        context.user_data.pop("state", None)
        await my_favorites(update, context)
    elif text == "🍳 Что приготовить":
        await cook_prompt(update, context)
    elif text == "🎲 Удиви меня":
        context.user_data.pop("state", None)
        await random_recipe(update, context)
    elif text == "🔍 Фильтры":
        context.user_data.pop("state", None)
        await filter_menu(update, context)
    elif text == "🍸 Коктейли":
        context.user_data.pop("state", None)
        await cocktail_menu(update, context)
    elif text == "🔍 Поиск коктейлей":
        context.user_data["state"] = "cocktail_search"
        await update.message.reply_text("Напиши название коктейля:")
    elif text == "🎲 Случайный коктейль":
        context.user_data.pop("state", None)
        await random_cocktail_handler(update, context)
    elif text == "🍹 Алкогольные":
        context.user_data.pop("state", None)
        await filter_cocktails_by_alcohol(update, context, "Alcoholic")
    elif text == "🧃 Безалкогольные":
        context.user_data.pop("state", None)
        await filter_cocktails_by_alcohol(update, context, "Non_Alcoholic")
    elif text == "← На главную":
        context.user_data.pop("state", None)
        await update.message.reply_text("Главное меню:", reply_markup=MAIN_KEYBOARD)
    elif text == "🔄 Перезапустить":
        context.user_data.clear()
        await start(update, context)
    elif text == "❓ Помощь":
        context.user_data.pop("state", None)
        await help_command(update, context)
    elif state == "cook":
        context.user_data.pop("state", None)
        await cook_suggest(update, context)
    elif state == "cocktail_search":
        context.user_data.pop("state", None)
        await search_cocktails(update, context)
    else:
        await search_recipes(update, context)


def main():
    token = os.getenv("BOT_TOKEN")
    if not token:
        logger.error("BOT_TOKEN environment variable not set!")
        return

    init_db()
    logger.info("Starting bots...")

    t = threading.Thread(target=run_assistant_bot, daemon=True)
    t.start()

    app = Application.builder().token(token).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("settings", settings_cmd))
    app.add_handler(CommandHandler("premium", premium_cmd))
    app.add_handler(CommandHandler("donate", donate_cmd))
    app.add_handler(PreCheckoutQueryHandler(pre_checkout_handler))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, handle_successful_payment))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(CallbackQueryHandler(callback_router))

    port = int(os.getenv("PORT", "10000"))
    render_url = os.getenv("RENDER_EXTERNAL_URL", f"https://food-recipe-bot.onrender.com")
    webhook_path = os.getenv("WEBHOOK_PATH", "/webhook")
    webhook_url = f"{render_url}{webhook_path}"
    logger.info("Starting webhook on port %d at %s", port, webhook_url)
    kwargs = dict(
        listen="0.0.0.0",
        port=port,
        url_path=webhook_path,
        webhook_url=webhook_url,
        allowed_updates=Update.ALL_TYPES,
    )
    if secret := os.getenv("WEBHOOK_SECRET"):
        kwargs["secret_token"] = secret
    app.run_webhook(**kwargs)


if __name__ == "__main__":
    main()
