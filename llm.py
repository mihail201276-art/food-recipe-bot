import os
import logging

import httpx
from openai import OpenAI

logger = logging.getLogger(__name__)


PROXYAPI_BASE = "https://api.proxyapi.ru/openai/v1"

def _log_usage(model: str, usage):
    if usage:
        logger.info("TOKEN_USAGE model=%s prompt=%d completion=%d total=%d",
                     model, usage.prompt_tokens, usage.completion_tokens, usage.total_tokens)


def _call_proxyapi(message: str, system_prompt: str, model: str = "gpt-4o-mini") -> str | None:
    key = os.getenv("PROXYAPI_KEY")
    if not key:
        return None
    try:
        client = OpenAI(api_key=key, base_url=PROXYAPI_BASE, timeout=30)
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": message})
        resp = client.chat.completions.create(model=model, messages=messages)
        _log_usage(model, resp.usage)
        return resp.choices[0].message.content
    except Exception as e:
        logger.warning("ProxyAPI %s failed: %s", model, e)
        return None


def _call_proxyapi_vision(image_url: str, prompt: str = "Что это за продукты? Что можно приготовить?") -> str | None:
    key = os.getenv("PROXYAPI_KEY")
    if not key:
        return None
    try:
        client = OpenAI(api_key=key, base_url=PROXYAPI_BASE, timeout=30)
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": image_url}},
                ],
            }],
            max_tokens=500,
        )
        _log_usage("gpt-4o-mini-vision", resp.usage)
        return resp.choices[0].message.content
    except Exception as e:
        logger.warning("ProxyAPI vision failed: %s", e)
        return None


def transcribe_audio(file_path: str) -> str | None:
    key = os.getenv("PROXYAPI_KEY")
    if not key:
        return None
    try:
        with open(file_path, "rb") as f:
            client = OpenAI(api_key=key, base_url=PROXYAPI_BASE, timeout=60)
            resp = client.audio.transcriptions.create(
                model="whisper-1",
                file=f,
                language="ru",
            )
            return resp.text
    except Exception as e:
        logger.warning("Whisper failed: %s", e)
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


def split_message(text: str, limit: int = 4000) -> list[str]:
    if len(text) <= limit:
        return [text]
    parts = []
    while text:
        if len(text) <= limit:
            parts.append(text)
            break
        split_at = text.rfind("\n", 0, limit)
        if split_at == -1:
            split_at = text.rfind(". ", 0, limit)
        if split_at == -1:
            split_at = limit
        else:
            split_at += 1
        parts.append(text[:split_at])
        text = text[split_at:].strip()
    return parts


def get_llm_response(message: str, system_prompt: str = "") -> str:
    reply = _call_proxyapi(message, system_prompt, "gpt-4o-mini")
    if reply:
        return reply

    reply = _call_proxyapi(message, system_prompt, "gemini/gemini-2.5-flash-lite")
    if reply:
        return reply

    logger.info("Falling back to ApiFreeLLM...")
    reply = _call_apifreellm(message)
    if reply:
        return reply

    return "⚠️ Все провайдеры недоступны. Попробуй позже."
