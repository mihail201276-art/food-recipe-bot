import sys
import os
import re
import asyncio
import base64
import io
import threading
from html import escape
import logging

from dotenv import load_dotenv

load_dotenv()

import sentry_sdk
from sentry_sdk.integrations.logging import LoggingIntegration

if dsn := os.getenv("SENTRY_DSN"):
    sentry_sdk.init(
        dsn=dsn,
        integrations=[LoggingIntegration()],
        traces_sample_rate=1.0,
    )

import httpx
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, LabeledPrice
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, PreCheckoutQueryHandler, filters, ContextTypes

from database import init_db, add_favorite, remove_favorite, get_favorites, is_favorite, update_rating, get_rating, get_translation, save_translation, get_profile, save_profile, set_premium, check_and_increment_translation, log_meal, get_daily_nutrition, get_recent_meals
from assistant_bot import run_assistant_bot, shutdown_event
import llm
from llm import split_message, transcribe_audio, _call_proxyapi_vision

from services.http_client import shared_async_client

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

from utils.keyboards import MAIN_KEYBOARD, NUTRITION_KEYBOARD, COCKTAIL_KEYBOARD
from services.http_client import shared_async_client
from services.rate_limiter import rate_limiter

from handlers.common import start, help_command, donate_cmd, back_to_main, back_to_search, back_to_favorites
from handlers.search import search_prompt, search_recipes
from handlers.recipes import show_recipe, full_recipe_handler, random_recipe, adapt_recipe, share_recipe
from handlers.favorites import add_favorite_handler, remove_favorite_handler, rate_recipe, my_favorites, view_favorite
from handlers.cocktails import cocktail_menu, search_cocktails, show_cocktail, random_cocktail_handler, filter_cocktails, filter_cocktails_by_alcohol, back_to_cocktail
from handlers.translation import translate_recipe, translate_cocktail
from handlers.filters import filter_menu, filter_list, filter_results
from handlers.premium import premium_cmd, premium_buy_handler, pre_checkout_handler, handle_successful_payment
from handlers.ai_recipe import generate_ai_recipe, ai_variation_recipe
from handlers.nutrition import nutrition_menu, nutrition_analyze, nutrition_analyze_prompt, nutri_log_handler, nutrition_daily_report, nutrition_add_water, nutrition_log_meal_prompt, nutrition_log_manual, recipe_nutrition
from handlers.media import handle_voice, handle_photo
from handlers.settings import settings_cmd, settings_callback
from handlers.shopping import cook_prompt, cook_suggest, shopping_list, meal_plan


async def callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id

    if not rate_limiter.is_allowed(user_id):
        await query.answer("🚀 Слишком много запросов. Подожди немного.", show_alert=True)
        return

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
    elif data.startswith("nutri_log_"):
        await nutri_log_handler(update, context)
    elif data.startswith("nutri_"):
        await recipe_nutrition(update, context)
    else:
        await query.answer()
        await query.edit_message_text(f"Неизвестная команда: {data}. /start")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if not rate_limiter.is_allowed(user_id):
        await update.message.reply_text("🚀 Слишком много запросов. Подожди немного.")
        return

    text = update.message.text.strip()

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
    elif text == "🥗 Нутрициолог":
        context.user_data.pop("state", None)
        await nutrition_menu(update, context)
    elif text == "🔬 Анализ рецепта":
        context.user_data["state"] = "nutrition_analyze"
        await update.message.reply_text("Отправь название блюда или описание продуктов:")
    elif text == "📊 Дневной отчёт":
        context.user_data.pop("state", None)
        await nutrition_daily_report(update, context)
    elif text == "💧 Вода +250мл":
        context.user_data.pop("state", None)
        await nutrition_add_water(update, context)
    elif text == "➕ Записать приём пищи":
        context.user_data["state"] = "nutrition_manual"
        await update.message.reply_text(
            "Напиши в формате:\n"
            "<i>Название, калории, белки, жиры, углеводы</i>\n\n"
            "Пример: <i>Овсянка, 300, 10, 5, 50</i>",
            parse_mode="HTML",
        )
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
    elif state == "nutrition_analyze":
        context.user_data.pop("state", None)
        await nutrition_analyze(update, context)
    elif state == "nutrition_manual":
        context.user_data.pop("state", None)
        await nutrition_log_manual(update, context)
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

    async def shutdown(_app: Application):
        logger.info("Shutting down...")
        shutdown_event.set()
        if t.is_alive():
            t.join(timeout=5)
        await shared_async_client.aclose()

    app.post_shutdown(shutdown)

    port = int(os.getenv("PORT", "10000"))
    render_url = os.getenv("RENDER_EXTERNAL_URL", "https://food-recipe-bot.onrender.com")
    webhook_path = os.getenv("WEBHOOK_PATH", "/webhook")
    webhook_url = f"{render_url}{webhook_path}"
    logger.info("Starting webhook on port %d at %s", port, webhook_url)

    import tornado.web

    class HealthHandler(tornado.web.RequestHandler):
        def get(self):
            self.write({"status": "ok"})
            self.finish()

    kwargs = dict(
        listen="0.0.0.0",
        port=port,
        url_path=webhook_path,
        webhook_url=webhook_url,
        allowed_updates=Update.ALL_TYPES,
        custom_webhook_app=tornado.web.Application([
            (r"/health", HealthHandler),
        ]),
    )
    if secret := os.getenv("WEBHOOK_SECRET"):
        kwargs["secret_token"] = secret
    app.run_webhook(**kwargs)


if __name__ == "__main__":
    main()
