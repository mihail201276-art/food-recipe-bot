import logging
from html import escape

import httpx
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from services.http_client import shared_async_client
from utils.keyboards import COCKTAIL_KEYBOARD
from llm import split_message

logger = logging.getLogger(__name__)

COCKTAILDB_BASE = "https://www.thecocktaildb.com/api/json/v1/1"
MAX_RESULTS = int(__import__("os").getenv("MAX_RESULTS", "8"))


async def cocktail_menu(update: Update, _context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🍸 Выбери действие:", reply_markup=COCKTAIL_KEYBOARD)


async def search_cocktails(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = (context.user_data.pop("voice_text", None) or update.message.text).strip().lower()
    logger.info("Searching cocktails: %s", query)
    await update.message.reply_chat_action("typing")
    try:
        resp = await shared_async_client.get(f"{COCKTAILDB_BASE}/search.php", params={"s": query})
        resp.raise_for_status()
        data = resp.json()
    except httpx.TimeoutException:
        await update.message.reply_text("⏱️ Таймаут запроса.")
        return
    except httpx.HTTPStatusError as e:
        logger.error("API error: %s", e.response.status_code)
        await update.message.reply_text("⚠️ Временная ошибка API.")
        return
    except Exception:
        logger.exception("Unexpected error in search_cocktails")
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
        resp = await shared_async_client.get(f"{COCKTAILDB_BASE}/lookup.php", params={"i": drink_id})
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
        logger.exception("Unexpected error in show_cocktail")
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
        resp = await shared_async_client.get(f"{COCKTAILDB_BASE}/random.php")
        resp.raise_for_status()
        data = resp.json()
    except httpx.TimeoutException:
        await target.reply_text("⏱️ Таймаут запроса.")
        return
    except httpx.HTTPStatusError as e:
        logger.error("API error: %s", e.response.status_code)
        await target.reply_text("⚠️ Временная ошибка API.")
        return
    except Exception:
        logger.exception("Unexpected error in random_cocktail_handler")
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
    params = {}
    if query.data == "cocktail_alcoholic":
        params = {"a": "Alcoholic"}
    elif query.data == "cocktail_non_alcoholic":
        params = {"a": "Non_Alcoholic"}
    else:
        return
    try:
        resp = await shared_async_client.get(f"{COCKTAILDB_BASE}/filter.php", params=params)
        resp.raise_for_status()
        data_json = resp.json()
    except httpx.TimeoutException:
        await query.message.reply_text("⏱️ Таймаут запроса.")
        return
    except httpx.HTTPStatusError as e:
        logger.error("API error: %s", e.response.status_code)
        await query.message.reply_text("⚠️ Временная ошибка API.")
        return
    except Exception:
        logger.exception("Unexpected error in filter_cocktails")
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
        resp = await shared_async_client.get(f"{COCKTAILDB_BASE}/filter.php", params={"a": alcoholic})
        resp.raise_for_status()
        data = resp.json()
    except httpx.TimeoutException:
        await update.message.reply_text("⏱️ Таймаут запроса.")
        return
    except httpx.HTTPStatusError as e:
        logger.error("API error: %s", e.response.status_code)
        await update.message.reply_text("⚠️ Временная ошибка API.")
        return
    except Exception:
        logger.exception("Unexpected error in filter_cocktails_by_alcohol")
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
