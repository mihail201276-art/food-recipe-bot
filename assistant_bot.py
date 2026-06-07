import os
import time
import logging

from dotenv import load_dotenv
import httpx
from llm import get_llm_response, split_message
from database import add_history, get_history

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
        add_history(user_id, "system", "")  # dummy call to trigger cleanup later
        import sqlite3
        from pathlib import Path
        db_path = Path(__file__).parent / "favorites.db"
        try:
            with sqlite3.connect(db_path) as conn:
                conn.execute("DELETE FROM chat_history WHERE user_id = ?", (user_id,))
                conn.commit()
        except Exception:
            pass
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

    # build single prompt with history
    prompt_parts = []
    for m in messages:
        if m["role"] == "system":
            continue
        prompt_parts.append(f"{m['role']}: {m['content']}")
    full_prompt = "\n".join(prompt_parts)

    reply = get_llm_response(full_prompt, SYSTEM_PROMPT)
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
