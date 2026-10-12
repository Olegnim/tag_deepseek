"""Сборка сообщений для модели."""

from __future__ import annotations

import json

from .glossary import Term, format_terms
from .langs import lang_name

JSON_SHAPE = '[{"id": 1, "text": "перевод"}, {"id": 2, "text": "перевод"}]'


def system_prompt(
    source: str,
    target: str,
    skills: str,
    terms: list[Term],
    fmt: str,
    recent: list[str],
    use_web: bool,
) -> str:
    lines = [
        f"Ты — профессиональный переводчик и редактор. Переводишь с "
        f"{lang_name(source, genitive=True)} на {lang_name(target)}.",
        "",
        "Верни ТОЛЬКО JSON-массив без пояснений и без markdown-обёрток:",
        JSON_SHAPE,
        "Для каждого id — ровно один перевод. Перенос строки внутри текста — «\\n».",
        "",
        "Общие правила:",
        "- Переводи смысл, а не слова; русский текст должен звучать естественно.",
        "- Ничего не добавляй и не выбрасывай: ни фактов, ни обращений, ни вводных слов.",
        "- Сохраняй количество строк внутри каждого блока и его структуру.",
        "- Числа, даты, единицы измерения и формулы оставляй точными; переводи только слова вокруг.",
        "- Имена и названия: устоявшийся русский вариант, иначе практическая транскрипция.",
        "- Соблюдай русскую типографику: кавычки «», тире —, без лишних пробелов.",
    ]
    if fmt == "srt":
        lines.append(
            "- Это субтитры SRT: переводи только реплику, номер и тайминг не трогай."
        )
    if use_web:
        lines.append(
            "- Если термин, имя или реалия непонятны или требуют проверки — сначала "
            "уточни через web_search (при необходимости fetch_url), затем переводи."
        )

    terms_block = format_terms(terms)
    if terms_block:
        lines += ["", terms_block]

    if recent:
        lines += ["", "Предыдущий перевод (контекст, повторно не переводи:", "---"]
        lines += recent
        lines.append("---")

    if skills:
        lines += ["", "Навыки и специфика:", "---", skills, "---"]

    return "\n".join(lines)


def user_prompt(segments: list, fmt: str) -> str:
    payload = [{"id": seg.seq, "text": seg.text} for seg in segments]
    return (
        f"Формат: {fmt}. Переведи блоки ({len(payload)} шт.) и верни JSON-массив "
        f"с теми же id:\n" + json.dumps(payload, ensure_ascii=False)
    )


def build_messages(
    source: str,
    target: str,
    skills: str,
    terms: list[Term],
    fmt: str,
    recent: list[str],
    segments: list,
    use_web: bool,
) -> list[dict]:
    return [
        {
            "role": "system",
            "content": system_prompt(source, target, skills, terms, fmt, recent, use_web),
        },
        {"role": "user", "content": user_prompt(segments, fmt)},
    ]
