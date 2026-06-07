import os
import time
import logging

from dotenv import load_dotenv
import httpx
from openai import OpenAI

load_dotenv()

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = "Ты вежливый и профессиональный личный помощник, работающий в Telegram."


def _call_proxyapi(message: str) -> str | None:
    key = os.getenv("PROXYAPI_KEY")
    if not key:
        return None
    try:
        client = OpenAI(api_key=key, base_url="https://api.proxyapi.ru/openai/v1", timeout=15)
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": message},
            ],
        )
        return resp.choices[0].message.content
    except Exception as e:
        logger.warning("ProxyAPI failed: %s", e)
        return None


def _call_apifreellm(message: str) -> str | None:
    key = os.getenv("APIFREEL_KEY")
    if not key:
        return None
    try:
        with httpx.Client(timeout=35) as client:
            resp = client.post(
                "https://apifreellm.com/api/v1/chat",
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {key}",
                },
                json={"message": message},
            )
            data = resp.json()
            if data.get("success"):
                return data["response"]
            logger.warning("ApiFreeLLM not ok: %s", data)
            return None
    except Exception as e:
        logger.warning("ApiFreeLLM failed: %s", e)
        return None


def get_llm_response(user_message: str) -> str:
    reply = _call_proxyapi(user_message)
    if reply:
        return reply

    logger.info("Falling back to ApiFreeLLM...")
    reply = _call_apifreellm(user_message)
    if reply:
        return reply

    return "Извини, все провайдеры недоступны. Попробуй позже."


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

    reply = get_llm_response(text)
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
        format="%(asctime)s [%(levelname)s] %(name)s): %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )
    run_assistant_bot()
