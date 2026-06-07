import os
import time
import logging

from dotenv import load_dotenv
import httpx
from llm import get_llm_response

load_dotenv()

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "Ты кулинарный помощник: рецепты, замены ингредиентов, диеты, "
    "тайминги, хранение продуктов. Отвечай кратко, по делу, на русском."
)


def _api_call(token: str, method: str, json_data: dict):
    with httpx.Client() as client:
        r = client.post(
            f"https://api.telegram.org/bot{token}/{method}",
            json=json_data,
            timeout=30,
        )
        return r.json()


def handle_update(update: dict, token: str):
    msg = update.get("message") or update.get("edited_message")
    if not msg:
        return
    chat_id = msg["chat"]["id"]
    text = msg.get("text", "")

    if not text:
        return

    if text == "/start":
        _api_call(token, "sendMessage", {
            "chat_id": chat_id,
            "text": "Привет! Я твой личный помощник. Задавай любые вопросы.",
        })
        return

    _api_call(token, "sendChatAction", {
        "chat_id": chat_id,
        "action": "typing",
    })

    reply = get_llm_response(text, SYSTEM_PROMPT)
    _api_call(token, "sendMessage", {
        "chat_id": chat_id,
        "text": reply,
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

        except Exception as e:
            logger.error("Polling error: %s", e)
            time.sleep(5)


if __name__ == "__main__":
    import sys
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )
    run_assistant_bot()
