import logging

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, LabeledPrice
from telegram.ext import ContextTypes

from database import get_profile, set_premium

logger = logging.getLogger(__name__)

TRANSLATE_DAILY_LIMIT = int(__import__("os").getenv("TRANSLATE_DAILY_LIMIT", "20"))
PREMIUM_DAILY_LIMIT = int(__import__("os").getenv("PREMIUM_DAILY_LIMIT", "100"))


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
