"""Поддерживаемые языки и режимы перевода."""

from __future__ import annotations

LANGUAGES: dict[str, dict[str, str]] = {
    "zh": {"nom": "китайский", "gen": "китайского", "skill": "zh-ru.md"},
    "ko": {"nom": "корейский", "gen": "корейского", "skill": "ko-ru.md"},
    "en": {"nom": "английский", "gen": "английского", "skill": "en-ru.md"},
    "ru": {"nom": "русский", "gen": "русского", "skill": ""},
}

# "auto" означает автоопределение языка источника.
SOURCE_CHOICES = ("zh", "ko", "en", "auto")

DEFAULT_SOURCE = "zh"
DEFAULT_TARGET = "ru"


def lang_name(code: str, genitive: bool = False) -> str:
    """Человекочитаемое название языка для промпта и подписей."""
    info = LANGUAGES.get(code)
    if not info:
        return code
    return info["gen"] if genitive else info["nom"]


def source_skill(source: str) -> str:
    """Имя файла навыка перевода для языка-источника ("" если нет)."""
    return LANGUAGES.get(source, {}).get("skill", "")
