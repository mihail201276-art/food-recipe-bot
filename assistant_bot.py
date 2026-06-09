import os
import time
import logging

from dotenv import load_dotenv
import httpx
from llm import get_llm_response, split_message
from database import add_history, get_history

load_dotenv()

logger = logging.getLogger(__name__)

_http = httpx.Client(
    timeout=httpx.Timeout(30.0, connect=10.0, read=25.0),
    limits=httpx.Limits(max_keepalive_connections=5, max_connections=10),
)

SYSTEM_PROMPT = (
    "Ты кулинарный помощник: рецепты, замены ингредиентов, диеты, "
    "тайминги, хранение продуктов. Отвечай кратко, по делу, на русском."
)


def _api_call(token: str, method: str, json_data: dict) -> dict | None:
    try:
        r = _http.post(
            f"https://api.telegram.org/bot{token}/{method}",
            json=json_data,
        )
        return r.json()
    except httpx.TimeoutException:
        logger.warning("Telegram API timeout: %s", method)
    except httpx.HTTPStatusError as e:
        logger.warning("Telegram API error %s: %s", method, e.response.status_code)
    except Exception:
        logger.exception("Telegram API call failed: %s", method)
    return None


def handle_update(update: dict, token: str):
    msg = update.get("message") or update.get("edited_message")
    if not msg:
        return
    chat_id = msg["chat"]["id"]
    user_id = msg["from"]["id"]
    text = msg.get("text", "")

    if not text:
        return

    if text == "/start":
        _api_call(token, "sendMessage", {
            "chat_id": chat_id,
            "text": "Привет! Я кулинарный помощник. Спрашивай про рецепты, замены ингредиентов, диеты, тайминги, хранение продуктов.",
        })
        return

    if text == "/clear":
        add_history(user_id, "system", "")
        import sqlite3
        from pathlib import Path
        try:
            db_path = Path(__file__).parent / "favorites.db"
            with sqlite3.connect(db_path) as conn:
                conn.execute("DELETE FROM chat_history WHERE user_id = ?", (user_id,))
                conn.commit()
        except Exception as e:
            logger.error("Failed to clear history for user %s: %s", user_id, e)
        _api_call(token, "sendMessage", {
            "chat_id": chat_id,
            "text": "История диалога очищена.",
        })
        return

    _api_call(token, "sendChatAction", {
        "chat_id": chat_id,
        "action": "typing",
    })

    add_history(user_id, "user", text)
    history = get_history(user_id)
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    for h in history:
        messages.append({"role": h["role"], "content": h["content"]})

    prompt_parts = []
    for m in messages:
        if m["role"] == "system":
            continue
        prompt_parts.append(f"{m['role']}: {m['content']}")
    full_prompt = "\n".join(prompt_parts)

    reply = get_llm_response(full_prompt, SYSTEM_PROMPT)
    if not reply:
        _api_call(token, "sendMessage", {
            "chat_id": chat_id,
            "text": "⚠️ Не удалось получить ответ от ИИ. Попробуй позже.",
        })
        return

    add_history(user_id, "assistant", reply)

    for part in split_message(reply):
        _api_call(token, "sendMessage", {
            "chat_id": chat_id,
            "text": part,
        })


def run_assistant_bot():
    token = os.getenv("ASSISTANT_BOT_TOKEN")
    if not token:
        logger.warning("ASSISTANT_BOT_TOKEN не задан — assistant бот не запущен")
        return

    logger.info("Assistant бот запущен (polling)...")
    offset = 0

    while True:
        try:
            data = _api_call(token, "getUpdates", {
                "offset": offset,
                "timeout": 30,
            })
            if not data.get("ok"):
                logger.warning("getUpdates not ok: %s", data)
                time.sleep(5)
                continue

            for update in data.get("result", []):
                handle_update(update, token)
                offset = update["update_id"] + 1

        except httpx.TimeoutException:
            logger.warning("Polling timeout, retrying...")
        except httpx.HTTPStatusError as e:
            logger.error("Polling HTTP error: %s", e.response.status_code)
            time.sleep(10)
        except Exception as e:
            logger.exception("Polling error")
            time.sleep(5)


if __name__ == "__main__":
    import sys
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )
    run_assistant_bot()
