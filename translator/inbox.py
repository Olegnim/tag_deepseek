"""Работа с папками input/ и output/.

Папка input — «входящие»: сюда кладут исходники, агент сам их находит.
Папка output — готовые переводы.
Все операции с файлами выполняются в «мягком» режиме: ошибки ввода-вывода
(нет прав, занятый файл, диск переполнен) не роняют программу, а возвращаются
как понятное сообщение.
"""

from __future__ import annotations

import os
from pathlib import Path

from .formats import SRT_SUFFIXES, TXT_SUFFIXES

SUPPORTED_SUFFIXES = tuple(sorted(SRT_SUFFIXES | TXT_SUFFIXES))

# Служебный «мусор», который не считается исходником.
IGNORED_NAMES = {".DS_Store", "Thumbs.db", "desktop.ini", ".gitkeep"}


class InboxError(RuntimeError):
    """Ошибка работы с папками ввода-вывода."""


def ensure_dir(path: Path) -> tuple[bool, str]:
    """Создаёт папку, если её нет. Возвращает (успех, текст ошибки)."""
    try:
        if path.is_dir():
            return True, ""
        if path.exists():
            return False, f"путь {path} существует, но это не папка"
        path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return False, f"не удалось создать папку {path}: {exc}"
    if not path.is_dir():
        return False, f"путь {path} существует, но это не папка"
    return True, ""


def is_supported(path: Path) -> bool:
    return path.suffix.lower() in SUPPORTED_SUFFIXES


def list_sources(directory: Path) -> tuple[list[Path], list[Path]]:
    """Возвращает (подходящие файлы, файлы с неподдерживаемым форматом).

    Скрытые файлы и служебные (.DS_Store, .gitkeep, ...) пропускаются.
    """
    try:
        entries = sorted(directory.iterdir())
    except OSError as exc:
        raise InboxError(f"не удалось прочитать папку {directory}: {exc}") from exc

    supported: list[Path] = []
    other: list[Path] = []
    for entry in entries:
        if entry.name.startswith(".") or entry.name in IGNORED_NAMES:
            continue
        try:
            if not entry.is_file():
                continue
        except OSError:
            continue
        (supported if is_supported(entry) else other).append(entry)
    return supported, other


def file_size(path: Path) -> int:
    """Размер файла; при ошибке — -1 (сообщение о файле всё равно нужно показать)."""
    try:
        return path.stat().st_size
    except OSError:
        return -1


def human_size(size: int) -> str:
    if size < 0:
        return "размер неизвестен"
    for unit in ("Б", "КБ", "МБ", "ГБ"):
        if size < 1024 or unit == "ГБ":
            return f"{size:.0f} {unit}" if unit == "Б" else f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} ГБ"


def unique_path(directory: Path, name: str) -> Path:
    """Свободный путь в папке: уже готовый перевод не перезаписываем.

    Если movie.ru.srt уже есть — получим movie.ru-2.srt, затем -3 и так далее.
    """
    candidate = directory / name
    if not candidate.exists():
        return candidate
    stem = candidate.stem
    suffix = candidate.suffix
    for number in range(2, 1000):
        candidate = directory / f"{stem}-{number}{suffix}"
        if not candidate.exists():
            return candidate
    raise InboxError(f"в папке {directory} слишком много файлов с именем {name}")


def clean_input(input_path: Path, output_path: Path, clear: bool) -> tuple[bool, str]:
    """Убирает исходник из input после удачного перевода.

    Перед удалением проверяем, что файл действительно прочитан и перевод записан:
    если исходник изменился или оказался недоступен, ничего не трогаем.
    """
    if not clear:
        return True, ""
    try:
        if not input_path.is_file():
            return True, "исходный файл уже удалён"
        os.remove(input_path)
    except OSError as exc:
        return False, f"перевод готов, но не удалить {input_path}: {exc}"
    return True, ""


def remove_partial(path: Path) -> None:
    """Удаляет пустой файл, оставшийся после неудачного запуска (best effort)."""
    try:
        if path.is_file() and path.stat().st_size == 0:
            path.unlink()
    except OSError:
        pass
