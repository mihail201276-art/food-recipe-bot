import os
import sys
import time
import logging

from dotenv import load_dotenv
import telebot
from openai import OpenAI

load_dotenv()

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = "Ты вежливый и профессиональный личный помощник, работающий в Telegram."


def get_llm_response(user_message: str) -> str:
    client = OpenAI(
        api_key=os.getenv("PROXYAPI_KEY"),
        base_url="https://api.proxyapi.ru/openai/v1",
    )
    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_message},
            ],
        )
        return response.choices[0].message.content
    except Exception as e:
        logger.error("LLM request failed: %s", e)
        return "Извини, произошла ошибка при обработке запроса. Попробуй позже."


def run_assistant_bot():
    token = os.getenv("ASSISTANT_BOT_TOKEN")
    if not token:
        logger.warning("ASSISTANT_BOT_TOKEN не задан — assistant бот не запущен")
        return

    bot = telebot.TeleBot(token)

    @bot.message_handler(commands=["start"])
    def handle_start(message):
        bot.send_message(
            message.chat.id,
            "Привет! Я твой личный помощник. Задавай любые вопросы.",
        )

    @bot.message_handler(func=lambda msg: True)
    def handle_text(message):
        bot.send_chat_action(message.chat.id, "typing")
        response = get_llm_response(message.text)
        bot.send_message(message.chat.id, response)

    logger.info("Assistant бот запущен (polling)...")
    while True:
        try:
            bot.infinity_polling()
        except Exception as e:
            logger.error("Assistant bot polling crashed: %s, restart in 5s", e)
            time.sleep(5)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )
    run_assistant_bot()
