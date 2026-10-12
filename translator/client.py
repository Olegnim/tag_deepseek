"""Клиент DeepSeek (OpenAI-совместимый API)."""

from __future__ import annotations

import time

from .config import api_key, api_key_env_name

RETRYABLE_NAMES = {
    "APIConnectionError", "APITimeoutError", "RateLimitError",
    "InternalServerError", "Timeout", "ConnectTimeout", "ReadTimeout",
}


class ApiError(RuntimeError):
    """Ошибка обращения к API DeepSeek."""


def is_retryable(error: Exception) -> bool:
    status = getattr(error, "status_code", None)
    if isinstance(status, int) and (status in (408, 409, 429) or 500 <= status < 600):
        return True
    return type(error).__name__ in RETRYABLE_NAMES


def missing_key_message(cfg: dict) -> str:
    env_name = api_key_env_name(cfg)
    return (
        f"Не задан ключ API. Укажите переменную окружения {env_name}\n"
        "  macOS / Linux:      export " + env_name + "=sk-...\n"
        "  Windows (PowerShell): $env:" + env_name + "=\"sk-...\"\n"
        "  Windows (cmd):        set " + env_name + "=sk-...\n"
        "Либо впишите ключ в поле api.api_key файла config.json."
    )


class DeepSeekClient:
    """Обёртка над OpenAI-клиентом: модель, повторы, ошибки."""

    def __init__(self, cfg: dict, log=print) -> None:
        api = cfg["api"]
        key = api_key(cfg)
        if not key:
            raise ApiError(missing_key_message(cfg))
        self.model = str(api.get("model") or "deepseek-flash")
        self.temperature = float(api.get("temperature", 0.3))
        self.max_retries = max(1, int(api.get("max_retries", 4)))
        self.log = log
        self._client = self._build(key, api)

    @staticmethod
    def _build(key: str, api: dict):
        try:
            from openai import OpenAI
        except ImportError as exc:  # pragma: no cover - зависит от окружения
            raise ApiError(
                "Не установлен пакет openai. Выполните: pip install -r requirements.txt"
            ) from exc
        return OpenAI(
            api_key=key,
            base_url=str(api.get("base_url") or "https://api.deepseek.com"),
            timeout=float(api.get("timeout", 180)),
        )

    def chat(self, messages: list, tools: list | None = None):
        """Запрос к модели с повторами при сетевых сбоях и 429/5xx."""
        last_error: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                kwargs: dict = {
                    "model": self.model,
                    "messages": messages,
                    "temperature": self.temperature,
                }
                if tools:
                    kwargs["tools"] = tools
                    kwargs["tool_choice"] = "auto"
                return self._client.chat.completions.create(**kwargs)
            except Exception as exc:  # noqa: BLE001 — тип ошибки задаёт SDK
                last_error = exc
                if attempt >= self.max_retries or not is_retryable(exc):
                    break
                delay = 2 ** (attempt - 1)
                self.log(
                    f"  ! сбой API ({type(exc).__name__}), повтор "
                    f"{attempt}/{self.max_retries - 1} через {delay} с"
                )
                time.sleep(delay)
        raise ApiError(f"Сбой API: {type(last_error).__name__}: {last_error}")
