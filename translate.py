#!/usr/bin/env python3
"""Агент-переводчик на DeepSeek: китайский / корейский / английский / японский → русский.

Форматы на входе: простой текст (.txt) и субтитры (.srt).
Результат пишется рядом с исходником: movie.srt → movie.ru.srt.

Примеры:
    python translate.py subtitles.srt
    python translate.py story.txt --source ko -o story.ru.txt
    python translate.py interview.srt --source en --no-web
    python translate.py manga.txt --source ja
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

# Консоль Windows по умолчанию cp1251 — иначе падают русские буквы и «✓».
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

from translator import __version__, formats
from translator import config as config_mod
from translator.client import ApiError, DeepSeekClient
from translator.glossary import load_glossary
from translator.langs import DEFAULT_SOURCE, DEFAULT_TARGET, SOURCE_CHOICES, lang_name
from translator.pipeline import Translator
from translator.skills import load_skills, skill_names

MENU = [
    ("zh", "Китайский → Русский (по умолчанию)"),
    ("ko", "Корейский → Русский"),
    ("en", "Английский → Русский"),
    ("ja", "Японский → Русский"),
    ("auto", "Определить язык автоматически"),
]

DETECT_PROMPT = (
    "Определи язык текста. Ответь одним кодом и больше ничем: "
    "zh — китайский, ko — корейский, en — английский, ja — японский.\n\nТекст:\n"
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Перевод текста и субтитров на русский с помощью DeepSeek.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Примеры:\n"
            "  python translate.py movie.srt\n"
            "  python translate.py book.txt --source ko\n"
            "  python translate.py subs.srt --source en -o subs.ru.srt --no-web"
        ),
    )
    parser.add_argument("input", help="входной файл (.txt или .srt)")
    parser.add_argument("-o", "--output", default=None,
                        help="куда писать перевод (по умолчанию <имя>.<язык>.<расширение>)")
    parser.add_argument("-s", "--source", default=None, choices=SOURCE_CHOICES,
                        help="язык оригинала: zh (по умолчанию), ko, en, ja, auto")
    parser.add_argument("--to", default=DEFAULT_TARGET, choices=("ru",),
                        help="язык перевода (по умолчанию ru)")
    parser.add_argument("-g", "--glossary", default=None,
                        help="файл терминологии (TSV), по умолчанию glossary/terms.tsv")
    parser.add_argument("--no-web", action="store_true",
                        help="запретить веб-поиск при переводе")
    parser.add_argument("--batch-blocks", type=int, default=None,
                        help="сколько блоков отправлять за один запрос (по умолчанию 40)")
    parser.add_argument("-c", "--config", default=None, help="путь к config.json")
    parser.add_argument("--version", action="version", version=f"translate.py {__version__}")
    return parser


def choose_source_interactive(default: str = DEFAULT_SOURCE) -> str:
    print("Режим перевода:")
    for i, (_, label) in enumerate(MENU, 1):
        print(f"  {i}) {label}")
    try:
        raw = input(f"Выбор [1 = {lang_name(default)}]: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return default
    if not raw:
        return default
    if raw in SOURCE_CHOICES:
        return raw
    if raw.isdigit() and 1 <= int(raw) <= len(MENU):
        return MENU[int(raw) - 1][0]
    print(f"Не понял «{raw}», беру режим по умолчанию: {lang_name(default)}")
    return default


def make_segment_source(path: Path, fmt: str, cfg: dict):
    if fmt == "srt":
        return lambda: formats.iter_srt_segments(path)
    translation = cfg["translation"]
    minimum = int(translation.get("min_block_chars", 500))
    maximum = int(translation.get("max_block_chars", 1500))
    return lambda: formats.iter_txt_segments(path, min_chars=minimum, max_chars=maximum)


def sample_text(make_segments, limit: int = 4000) -> str:
    chunks: list[str] = []
    size = 0
    for segment in make_segments():
        if segment.passthrough or not segment.text.strip():
            continue
        chunks.append(segment.text)
        size += len(segment.text)
        if size >= limit:
            break
    return "\n".join(chunks)


def detect_language(client: DeepSeekClient, sample: str) -> str | None:
    response = client.chat([{"role": "user", "content": DETECT_PROMPT + sample[:4000]}])
    answer = (response.choices[0].message.content or "").strip().lower()
    for code in ("zh", "ko", "en", "ja"):
        if code in answer:
            return code
    return None


def main() -> int:
    args = build_parser().parse_args()
    cfg = config_mod.load_config(args.config)

    input_path = Path(args.input).expanduser()
    if not input_path.is_file():
        print(f"Файл не найден: {input_path}")
        return 2

    fmt = formats.detect_format(input_path)
    make_segments = make_segment_source(input_path, fmt, cfg)

    source = args.source
    if source is None:
        default_source = cfg["translation"].get("source", DEFAULT_SOURCE)
        # Меню только в интерактивном терминале, чтобы не мешать скриптам и конвейерам.
        source = choose_source_interactive(default_source) if sys.stdin.isatty() else default_source

    try:
        client = DeepSeekClient(cfg)
    except ApiError as exc:
        print(exc)
        return 3

    if source == "auto":
        sample = sample_text(make_segments)
        if not sample.strip():
            print("Не удалось прочитать текст для автоопределения языка.")
            return 2
        print("Определяю язык оригинала...")
        detected = detect_language(client, sample)
        if detected is None:
            print("Язык определить не удалось — укажите его флагом --source (zh/ko/en/ja).")
            return 2
        source = detected
        print(f"Определён язык: {lang_name(source)}")

    if args.batch_blocks:
        cfg["translation"]["batch_blocks"] = args.batch_blocks

    skills_dir = config_mod.project_path(cfg, "skills_dir", "skills")
    glossary_path = (
        Path(args.glossary).expanduser()
        if args.glossary
        else config_mod.project_path(cfg, "glossary_file", "glossary/terms.tsv")
    )

    skills_text = load_skills(skills_dir, skill_names(source, fmt))
    terms = load_glossary(glossary_path)
    use_web = not args.no_web and bool(cfg["translation"].get("web_search", True))

    output_path = (
        Path(args.output).expanduser()
        if args.output
        else formats.default_output_path(input_path, args.to)
    )
    if output_path.parent and not output_path.parent.exists():
        output_path.parent.mkdir(parents=True, exist_ok=True)

    total = sum(1 for s in make_segments() if not s.passthrough)

    print(f"Формат:       {fmt}")
    print(f"Режим:        {lang_name(source)} → {lang_name(args.to)}")
    print(f"Файл:         {input_path}")
    print(f"Результат:    {output_path}")
    print(f"Блоков:       {total}")
    print(f"Терминов:     {len(terms)} из {glossary_path}")
    print(f"Веб-поиск:    {'включён' if use_web else 'выключен'}")
    print("-" * 60)

    translator = Translator(
        client=client,
        cfg=cfg,
        source=source,
        target=args.to,
        skills_text=skills_text,
        terms=terms,
        fmt=fmt,
        use_web=use_web,
    )

    started = time.time()
    try:
        with open(output_path, "w", encoding="utf-8", newline="\n") as out_fh:
            stats = translator.run(make_segments(), out_fh, total=total)
    except ApiError as exc:
        print(f"\nОшибка API: {exc}")
        return 4
    except KeyboardInterrupt:
        elapsed = time.time() - started
        print(f"\nПрервано пользователем через {elapsed:.1f} с.")
        print(f"Частичный перевод уже записан в {output_path}")
        return 130

    print("-" * 60)
    print(f"Готово за {stats.elapsed:.1f} с.")
    print(f"  блоков:   {stats.blocks} (пакетов: {stats.batches})")
    if stats.failed:
        print(f"  без перевода: {stats.failed} (оставлен оригинал)")
    if stats.tool_calls:
        print(f"  веб-запросов: {stats.tool_calls}")
    print(f"  токенов:  {stats.total_tokens} "
          f"(prompt {stats.prompt_tokens} / completion {stats.completion_tokens})")
    print(f"  файл:     {output_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
