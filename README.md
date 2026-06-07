# 🍽 Food Recipe Bot — Кулинарный Telegram-бот

Два бота в одном процессе:
- **@Food_Recipe_Bot** — поиск рецептов через TheMealDB, избранное, ИИ-перевод
- **@Smart_pomogator_bot** — кулинарный ИИ-помощник (замены, диеты, тайминги)

## Функции

| Функция | Описание |
|---|---|
| 🔍 Поиск рецептов | На любом языке (ИИ-перевод запроса) |
| 📚 Мои рецепты | Избранное + оценки ⭐ + список покупок 🛒 |
| 🍳 Что приготовить | Напиши продукты → ИИ предложит блюда |
| 🎲 Удиви меня | Случайный рецепт из 300+ |
| 🔍 Фильтры | По категории, кухне, ингредиенту |
| 📸 Фото холодильника | Vision AI анализирует фото |
| 🎤 Голос | Whisper → распознавание → поиск |
| 🌐 Перевод | Любой рецепт → русский (кэш) |
| 🥛 Адаптация | Без лактозы / Упростить / На 2 порции |
| 📅 План на неделю | 7 рецептов + список покупок |
| ⚙️ Профиль | Аллергии, диета, без глютена |

## Деплой

### Render (рекомендуется)

1. Fork репозитория на GitHub
2. На [render.com](https://render.com) → New Web Service
3. Подключи GitHub-репозиторий
4. Настройки:
   - **Runtime**: Python 3
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `python bot.py`
5. Добавь Environment Variables:
   - `BOT_TOKEN`, `ASSISTANT_BOT_TOKEN`, `PROXYAPI_KEY`, `APIFREEL_KEY`
6. Deploy → Manual Deploy

### Docker (самостоятельно)

```bash
cp .env.example .env
# заполни .env

docker compose up -d
```

## Переменные окружения

| Переменная | Обязательно | Описание |
|---|---|---|
| `BOT_TOKEN` | ✅ | Токен @Food_Recipe_Bot от BotFather |
| `ASSISTANT_BOT_TOKEN` | ❌ | Токен @Smart_pomogator_bot |
| `PROXYAPI_KEY` | ❌ | Ключ ProxyAPI (gpt-4o-mini) |
| `APIFREEL_KEY` | ❌ | Ключ ApiFreeLLM (бесплатный fallback) |
| `RENDER_EXTERNAL_URL` | ❌ | URL на Render для webhook |
| `DONATION_URL` | ❌ | Ссылка для донатов |

## Монетизация

- ⭐ **Премиум**: 100 переводов/день вместо 20
- ☕ **Донаты** через `/donate`
- Связь: @mihail201276

## Технологии

- Python 3.12 · python-telegram-bot 22.7 · httpx · OpenAI SDK
- SQLite · TheMealDB API · ProxyAPI · Whisper · GPT-4o-mini Vision
- Render (webhook) + polling в фоновом потоке

## Лицензия

MIT
