"""Загрузка настроек агента-переводчика.

Настройки лежат в config.json рядом с translate.py. Значения по умолчанию
заданы здесь же, поэтому файла конфигурации может и не быть.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

# Корень проекта — папка, где лежит пакет translator/.
AGENT_HOME = Path(__file__).resolve().parent.parent

DEFAULTS: dict = {
    "api": {
        "api_key_env": "DEEPSEEK_API_KEY",
        "api_key": "",
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-flash",
        "temperature": 0.3,
        "timeout": 180,
        "max_retries": 4,
    },
    "translation": {
        "source": "zh",          # zh | ko | en | auto
        "target": "ru",
        "batch_blocks": 40,      # сколько блоков отправлять за один запрос
        "batch_chars": 6000,     # и сколько символов максимум в одном запросе
        "context_blocks": 3,     # сколько предыдущих переводов давать как контекст
        "max_tool_steps": 4,     # максимум шагов веб-поиска внутри одного пакета
        "web_search": True,
        "min_block_chars": 500,  # .txt: копить строки в блок до этого размера
        "max_block_chars": 1500, # .txt: принудительно закрывать блок на этом размере
    },
    "glossary_file": "glossary/terms.tsv",
    "skills_dir": "skills",
    "tools": {
        "web": {
            "enabled": True,
            "timeout": 20,
            "max_chars": 12000,
            "results": 5,
        }
    },
}


def _deep_merge(base: dict, override: dict) -> dict:
    """Рекурсивно накладывает override на копию base."""
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def default_config_path() -> Path:
    return AGENT_HOME / "config.json"


def load_config(path: str | os.PathLike[str] | None = None) -> dict:
    """Возвращает настройки: значения из config.json поверх значений по умолчанию."""
    config_path = Path(path).expanduser() if path else default_config_path()
    data: dict = {}
    if config_path.is_file():
        try:
            data = json.loads(config_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise SystemExit(f"Ошибка в {config_path}: некорректный JSON ({exc})") from exc
    cfg = _deep_merge(DEFAULTS, data if isinstance(data, dict) else {})
    cfg["_config_path"] = str(config_path)
    return cfg


def project_path(cfg: dict, key: str, default: str) -> Path:
    """Путь из конфига; относительные пути считаются от корня проекта."""
    raw = cfg.get(key) or default
    path = Path(str(raw)).expanduser()
    if not path.is_absolute():
        path = AGENT_HOME / path
    return path


def api_key(cfg: dict) -> str:
    """Ключ API: сначала из конфига, потом из переменной окружения."""
    value = str(cfg["api"].get("api_key") or "").strip()
    if value:
        return value
    env_name = str(cfg["api"].get("api_key_env") or "DEEPSEEK_API_KEY")
    return (os.environ.get(env_name) or "").strip()


def api_key_env_name(cfg: dict) -> str:
    return str(cfg["api"].get("api_key_env") or "DEEPSEEK_API_KEY")
