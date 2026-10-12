#!/usr/bin/env python3
"""Агент-переводчик на DeepSeek: китайский / корейский / английский / японский → русский.

Форматы на входе: простой текст (.txt) и субтитры (.srt).

Два способа работы:
  1) папка input: положите туда файл и запустите `python translate.py` — агент
     найдёт источник, предложит его перевести и запишет результат в output;
     после удачного перевода исходник из input убирается;
  2) явно указать файл: `python translate.py movie.srt`.

Примеры:
    python translate.py
    python translate.py movie.srt
    python translate.py book.txt --source ko -o book.ru.txt
    python translate.py --source ja
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

from translator import __version__, formats, inbox
from translator import config as config_mod
from translator.client import ApiError, DeepSeekClient
from translator.glossary import load_glossary
from translator.langs import DEFAULT_SOURCE, DEFAULT_TARGET, SOURCE_CHOICES, lang_name
from translator.pipeline import Stats, Translator
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
            "  python translate.py                 # взять файл из папки input\n"
            "  python translate.py movie.srt       # явно указать файл\n"
            "  python translate.py book.txt --source ko\n"
            "  python translate.py --source ja --no-web"
        ),
    )
    parser.add_argument("input", nargs="?", default=None,
                        help="входной файл (.txt или .srt); без него берётся папка input")
    parser.add_argument("-o", "--output", default=None,
                        help="куда писать перевод (по умолчанию папка output)")
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
    parser.add_argument("--input-dir", default=None,
                        help="папка с исходниками (по умолчанию input/)")
    parser.add_argument("--output-dir", default=None,
                        help="папка для переводов (по умолчанию output/)")
    parser.add_argument("-c", "--config", default=None, help="путь к config.json")
    parser.add_argument("--version", action="version", version=f"translate.py {__version__}")
    return parser


def confirm(question: str, default: bool = True) -> bool:
    """Спрашивает подтверждение; без терминала — соглашается молча."""
    if not sys.stdin.isatty():
        return default
    suffix = " [Y/n]: " if default else " [y/N]: "
    try:
        answer = input(question + suffix).strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return False
    if not answer:
        return default
    return answer in ("y", "yes", "д", "да", "1")


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
    """Фабрика генераторов блоков: каждый вызов читает файл заново."""
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


def resolve_source(args_source: str | None, cfg: dict) -> str:
    if args_source:
        return args_source
    default_source = cfg["translation"].get("source", DEFAULT_SOURCE)
    # Меню только в интерактивном терминале, чтобы не мешать скриптам и конвейерам.
    return choose_source_interactive(default_source) if sys.stdin.isatty() else default_source


def translate_file(
    path: Path,
    output_path: Path,
    source: str,
    target: str,
    cfg: dict,
    client: DeepSeekClient,
    args: argparse.Namespace,
    clear_input: bool,
    skills_dir: Path,
    glossary_path: Path,
) -> tuple[bool, str, Stats | None]:
    """Переводит один файл. Возвращает (успех, сообщение, статистика).

    Никогда не бросает ошибки ввода-вывода и API: они превращаются в сообщение,
    чтобы пакетный режим не прерывался на одном проблемном файле.
    """
    try:
        fmt = formats.detect_format(path)
        make_segments = make_segment_source(path, fmt, cfg)
        total = sum(1 for s in make_segments() if not s.passthrough)
    except OSError as exc:
        return False, f"не удалось прочитать {path.name}: {exc}", None
    except UnicodeError as exc:
        return False, f"{path.name}: ошибка кодировки ({exc})", None

    if total == 0:
        return False, f"{path.name}: нет текста для перевода", None

    actual_source = source
    if source == "auto":
        try:
            sample = sample_text(make_segments)
            detected = detect_language(client, sample) if sample.strip() else None
        except OSError as exc:
            return False, f"не удалось прочитать {path.name}: {exc}", None
        except ApiError as exc:
            return False, f"не удалось определить язык: {exc}", None
        if detected is None:
            return False, "язык не определён — укажите --source (zh/ko/en/ja)", None
        actual_source = detected

    skills_text = load_skills(skills_dir, skill_names(actual_source, fmt))
    terms = load_glossary(glossary_path)
    use_web = not args.no_web and bool(cfg["translation"].get("web_search", True))

    translator = Translator(
        client=client,
        cfg=cfg,
        source=actual_source,
        target=target,
        skills_text=skills_text,
        terms=terms,
        fmt=fmt,
        use_web=use_web,
    )

    print(f"    {fmt} | {lang_name(actual_source)} → {lang_name(target)} | блоков: {total}")

    try:
        with open(output_path, "w", encoding="utf-8", newline="\n") as out_fh:
            stats = translator.run(make_segments(), out_fh, total=total)
    except OSError as exc:
        inbox.remove_partial(output_path)
        return False, f"ошибка записи в {output_path}: {exc}", None
    except ApiError as exc:
        inbox.remove_partial(output_path)
        return False, f"ошибка API: {exc}", None
    except KeyboardInterrupt:
        raise

    details = f"{stats.blocks} блоков за {stats.elapsed:.1f} с"
    if stats.tool_calls:
        details += f", веб-запросов: {stats.tool_calls}"
    if stats.total_tokens:
        details += f", токенов: {stats.total_tokens}"

    if stats.failed:
        return True, f"{details}. Без перевода: {stats.failed} — исходник оставлен в input", stats

    cleaned, clean_message = inbox.clean_input(path, output_path, clear_input)
    if not cleaned:
        return True, f"{details}. {clean_message}", stats
    return True, details, stats


def main() -> int:
    args = build_parser().parse_args()

    try:
        cfg = config_mod.load_config(args.config)
    except SystemExit as exc:
        print(exc)
        return 2

    input_dir = (Path(args.input_dir).expanduser() if args.input_dir
                 else config_mod.project_path(cfg, "input_dir", "input"))
    output_dir = (Path(args.output_dir).expanduser() if args.output_dir
                  else config_mod.project_path(cfg, "output_dir", "output"))

    for folder in (input_dir, output_dir):
        created, error = inbox.ensure_dir(folder)
        if not created:
            print(f"Ошибка: {error}")
            return 5

    # --- какие файлы переводим ---
    clear_input = False
    if args.input:
        path = Path(args.input).expanduser()
        if not path.is_file():
            print(f"Файл не найден: {path}")
            return 2
        jobs = [path]
    else:
        try:
            sources, skipped = inbox.list_sources(input_dir)
        except inbox.InboxError as exc:
            print(f"Ошибка: {exc}")
            return 5
        if skipped:
            names = ", ".join(p.name for p in skipped)
            print(f"Пропускаю файлы неподдерживаемого формата: {names}")
        if not sources:
            print(f"В папке {input_dir} нет файлов для перевода.")
            print("Положите туда .txt или .srt — агент найдёт файл сам.")
            print(f"Готовый перевод запишется в {output_dir}.")
            return 0
        print(f"Найдено в {input_dir}:")
        for path in sources:
            size = inbox.human_size(inbox.file_size(path))
            print(f"  • {path.name} ({size})")
        if not confirm(f"Перевести ({len(sources)})?"):
            print("Отменено.")
            return 0
        jobs = sources
        clear_input = True

    source = resolve_source(args.source, cfg)

    try:
        client = DeepSeekClient(cfg)
    except ApiError as exc:
        print(exc)
        return 3

    if args.batch_blocks:
        cfg["translation"]["batch_blocks"] = args.batch_blocks

    skills_dir = config_mod.project_path(cfg, "skills_dir", "skills")
    glossary_path = (Path(args.glossary).expanduser() if args.glossary
                     else config_mod.project_path(cfg, "glossary_file", "glossary/terms.tsv"))

    fixed_output: Path | None = None
    if args.output:
        if len(jobs) == 1:
            fixed_output = Path(args.output).expanduser()
            created, error = inbox.ensure_dir(fixed_output.parent)
            if not created:
                print(f"Ошибка: {error}")
                return 5
        else:
            print("Флаг -o/--output задан, но файлов несколько — игнорирую его.")

    print("=" * 60)
    started = time.time()
    done = 0
    interrupted = False

    for index, path in enumerate(jobs, 1):
        if len(jobs) > 1:
            print(f"[{index}/{len(jobs)}] {path.name}")
        output_path = fixed_output or inbox.unique_path(
            output_dir, f"{path.stem}.{args.to}{path.suffix}"
        )
        try:
            success, message, _ = translate_file(
                path=path,
                output_path=output_path,
                source=source,
                target=args.to,
                cfg=cfg,
                client=client,
                args=args,
                clear_input=clear_input,
                skills_dir=skills_dir,
                glossary_path=glossary_path,
            )
        except KeyboardInterrupt:
            print(f"\nПрервано пользователем. Частичный перевод: {output_path}")
            interrupted = True
            break

        if success:
            done += 1
            print(f"    ✓ {message}")
            print(f"      → {output_path}")
        else:
            print(f"    ✗ {message}")

    print("=" * 60)
    print(f"Итог: переведено {done} из {len(jobs)} за {time.time() - started:.1f} с.")
    if interrupted:
        return 130
    if done == 0:
        return 4
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nПрервано.")
        sys.exit(130)
