import logging

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from database import get_profile, save_profile

logger = logging.getLogger(__name__)

TRANSLATE_DAILY_LIMIT = int(__import__("os").getenv("TRANSLATE_DAILY_LIMIT", "20"))
PREMIUM_DAILY_LIMIT = int(__import__("os").getenv("PREMIUM_DAILY_LIMIT", "100"))


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
