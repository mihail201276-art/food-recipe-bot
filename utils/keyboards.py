from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup

MAIN_KEYBOARD = ReplyKeyboardMarkup(
    [["🔍 Поиск рецептов", "📚 Мои рецепты"],
     ["🍳 Что приготовить", "🎲 Удиви меня"],
     ["🥗 Нутрициолог", "🍸 Коктейли"],
     ["🔍 Фильтры", "❓ Помощь"],
     ["🔄 Перезапустить"]],
    resize_keyboard=True,
)

NUTRITION_KEYBOARD = ReplyKeyboardMarkup(
    [["🔬 Анализ рецепта", "📊 Дневной отчёт"],
     ["💧 Вода +250мл", "➕ Записать приём пищи"],
     ["← На главную"]],
    resize_keyboard=True,
)

COCKTAIL_KEYBOARD = ReplyKeyboardMarkup(
    [["🔍 Поиск коктейлей", "🎲 Случайный коктейль"],
     ["🍹 Алкогольные", "🧃 Безалкогольные"],
     ["← На главную"]],
    resize_keyboard=True,
)
