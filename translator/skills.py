"""Загрузка навыков перевода из папки skills/."""

from __future__ import annotations

from pathlib import Path

from .langs import source_skill


def skill_names(source: str, fmt: str) -> list[str]:
    """Набор навыков для пары языков и формата."""
    names = ["base.md"]
    lang_skill = source_skill(source)
    if lang_skill:
        names.append(lang_skill)
    if fmt == "srt":
        names.append("subtitles.md")
    return names


def load_skills(skills_dir: Path, names: list[str]) -> str:
    """Склеивает найденные файлы навыков в один текст для промпта."""
    chunks: list[str] = []
    for name in names:
        path = skills_dir / name
        if path.is_file():
            text = path.read_text(encoding="utf-8").strip()
            if text:
                chunks.append(text)
    return "\n\n".join(chunks)
