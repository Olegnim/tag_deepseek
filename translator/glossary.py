"""Глоссарий терминов.

Формат файла — TSV (столбцы через tab), строки с «#» игнорируются:

    lang<TAB>source<TAB>target<TAB>note      # полная форма
    source<TAB>target<TAB>note               # краткая форма (для любого языка)

lang — код языка источника (zh / ko / en) или «*» / пусто для всех языков.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .langs import LANGUAGES


@dataclass
class Term:
    lang: str
    source: str
    target: str
    note: str = ""


def load_glossary(path: Path) -> list[Term]:
    """Читает глоссарий; отсутствие файла — это не ошибка."""
    if not path.is_file():
        return []
    terms: list[Term] = []
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip("\n")
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        fields = [f.strip() for f in line.split("\t")]
        if len(fields) < 2:
            continue
        if fields[0] in LANGUAGES or fields[0] in ("*", ""):
            if len(fields) < 3:
                continue
            lang = "" if fields[0] in ("*", "") else fields[0]
            source, target = fields[1], fields[2]
            note = fields[3] if len(fields) > 3 else ""
        else:
            lang = ""
            source, target = fields[0], fields[1]
            note = fields[2] if len(fields) > 2 else ""
        if source and target:
            terms.append(Term(lang=lang, source=source, target=target, note=note))
    return terms


def select_terms(terms: list[Term], source_lang: str, text: str, limit: int = 120) -> list[Term]:
    """Оставляет термины нужного языка, встречающиеся в тексте блока."""
    haystack = text.lower()
    selected: list[Term] = []
    for term in terms:
        if term.lang and term.lang != source_lang:
            continue
        if term.source.lower() in haystack:
            selected.append(term)
            if len(selected) >= limit:
                break
    return selected


def format_terms(terms: list[Term]) -> str:
    """Готовит блок терминологии для системного промпта."""
    if not terms:
        return ""
    lines = ["Терминология (обязательна к использованию в переводе):"]
    for term in terms:
        line = f"- {term.source} → {term.target}"
        if term.note:
            line += f" ({term.note})"
        lines.append(line)
    return "\n".join(lines)
