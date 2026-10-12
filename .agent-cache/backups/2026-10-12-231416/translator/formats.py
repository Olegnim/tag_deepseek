"""Чтение входных форматов и потоковая запись перевода.

Поддерживаются два формата:
  * "srt" — субтитры (номер, тайминг, текст блока);
  * "txt" — простой текст.

Всё построено на генераторах и записи «блок за блоком»: файл на 300+ блоков
(и любой большой .txt) обрабатывается потоково, результат не держится в памяти
целиком.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

SRT_SUFFIXES = {".srt", ".vtt"}
TXT_SUFFIXES = {".txt", ".text", ".md"}


@dataclass
class Segment:
    """Один блок перевода."""

    seq: int = 0              # порядковый номер блока в задании (1..N)
    text: str = ""            # исходный текст (внутри могут быть переносы строк)
    index: str = ""           # номер блока в SRT
    timing: str = ""          # строка тайминга SRT
    glue: bool = False        # True: следующая часть — продолжение этой же строки
    passthrough: bool = False # True: писать как есть, моделью не переводить
    translated: str = ""      # результат перевода


def detect_format(path: Path) -> str:
    """Определяет формат по расширению, при неясности — по содержимому."""
    suffix = path.suffix.lower()
    if suffix in SRT_SUFFIXES:
        return "srt"
    if suffix in TXT_SUFFIXES:
        return "txt"
    try:
        with open(path, "r", encoding="utf-8-sig", errors="replace") as fh:
            for i, line in enumerate(fh):
                if i >= 40:
                    break
                if "-->" in line:
                    return "srt"
    except OSError:
        pass
    return "txt"


def iter_lines(path: Path) -> Iterator[str]:
    """Строки файла без символов перевода строки (UTF-8, BOM отбрасывается)."""
    with open(path, "r", encoding="utf-8-sig", errors="replace") as fh:
        for line in fh:
            yield line.rstrip("\n").rstrip("\r")


# ---------------------------------------------------------------- SRT

def iter_srt_segments(path: Path) -> Iterator[Segment]:
    """Разбирает .srt на блоки, не загружая файл целиком."""
    block: list[str] = []
    for line in iter_lines(path):
        if line.strip() == "":
            if block:
                yield _srt_block_to_segment(block)
                block = []
        else:
            block.append(line)
    if block:
        yield _srt_block_to_segment(block)


def _srt_block_to_segment(lines: list[str]) -> Segment:
    index = ""
    timing = ""
    text_lines = lines
    if len(lines) >= 2 and lines[0].strip().isdigit() and "-->" in lines[1]:
        index = lines[0].strip()
        timing = lines[1].strip()
        text_lines = lines[2:]
    elif "-->" in lines[0]:
        timing = lines[0].strip()
        text_lines = lines[1:]
    text = "\n".join(text_lines).strip("\n")
    return Segment(text=text, index=index, timing=timing)


# ---------------------------------------------------------------- TXT

_SENTENCE_END = "。！？…！？"
_LATIN_END = ".!?"


def iter_txt_segments(path: Path, min_chars: int = 500, max_chars: int = 1500) -> Iterator[Segment]:
    """Разбивает простой текст на блоки, сохраняя разбиение на строки.

    Последовательные непустые строки копятся в один блок (чтобы у модели был
    контекст), но пустые строки и границы строк сохраняются. Строка длиннее
    max_chars режется по предложениям, а части помечаются glue=True — при
    записи они склеиваются обратно в одну строку.
    """
    buffer: list[str] = []
    buffer_len = 0

    for line in iter_lines(path):
        if not line.strip():
            if buffer:
                yield Segment(text="\n".join(buffer))
                buffer, buffer_len = [], 0
            yield Segment(text="", passthrough=True)
            continue

        if buffer and buffer_len >= min_chars and _ends_sentence(line):
            yield Segment(text="\n".join(buffer))
            buffer, buffer_len = [], 0

        if len(line) > max_chars:
            if buffer:
                yield Segment(text="\n".join(buffer))
                buffer, buffer_len = [], 0
            pieces = split_long_text(line, max_chars)
            for i, piece in enumerate(pieces):
                yield Segment(text=piece, glue=i < len(pieces) - 1)
            continue

        buffer.append(line)
        buffer_len += len(line)
        if buffer_len >= max_chars:
            yield Segment(text="\n".join(buffer))
            buffer, buffer_len = [], 0

    if buffer:
        yield Segment(text="\n".join(buffer))


def _ends_sentence(line: str) -> bool:
    stripped = line.rstrip()
    if not stripped:
        return False
    last = stripped[-1]
    return last in _SENTENCE_END or last in _LATIN_END


def split_long_text(text: str, max_chars: int) -> list[str]:
    """Режет слишком длинную строку на части по границам предложений."""
    if len(text) <= max_chars:
        return [text]
    soft = max(200, max_chars // 2)
    pieces: list[str] = []
    start = 0
    length = len(text)
    for i, char in enumerate(text):
        current = i - start + 1
        is_end = char in _SENTENCE_END
        if not is_end and char in _LATIN_END:
            nxt = text[i + 1] if i + 1 < length else ""
            is_end = nxt == "" or nxt.isspace()
        if (is_end and current >= soft) or current >= max_chars:
            pieces.append(text[start:i + 1])
            start = i + 1
    if start < length:
        pieces.append(text[start:])
    return pieces


# ---------------------------------------------------------------- запись

def write_segment(fh, fmt: str, seg: Segment, text: str) -> None:
    """Пишет один блок результата в открытый файл."""
    if fmt == "srt":
        index = seg.index or str(seg.seq)
        fh.write(f"{index}\n")
        if seg.timing:
            fh.write(f"{seg.timing}\n")
        fh.write((text or "").strip("\n") + "\n\n")
        return

    if seg.passthrough:
        fh.write("\n")
    elif seg.glue:
        fh.write(text or "")
    else:
        fh.write((text or "") + "\n")


def default_output_path(input_path: Path, target: str) -> Path:
    """movie.srt + ru -> movie.ru.srt; story.txt + ru -> story.ru.txt."""
    return input_path.with_name(f"{input_path.stem}.{target}{input_path.suffix}")
