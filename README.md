# translate.py — агент-переводчик на DeepSeek

Переводит **с китайского, корейского, английского и японского на русский** с помощью LLM
DeepSeek. Понимает два формата: **простой текст (`.txt`)** и **субтитры (`.srt`)**.
Результат пишется рядом с исходником (`movie.srt` → `movie.ru.srt`).

Особенности:

- режим по умолчанию — **китайский → русский**, меняется при старте (меню) или
  флагом `--source`;
- **потоковая запись**: перевод сбрасывается на диск блок за блоком, поэтому файл
  на 300+ субтитров и большие тексты обрабатываются без роста памяти;
- **специфические навыки перевода** (`skills/`) и **глоссарий терминологии**
  (`glossary/terms.tsv`) подставляются в промпт;
- **веб-поиск**: агент сам уточняет термины, имена и реалии через DuckDuckGo;
- работает на **Windows 11, macOS, Ubuntu/Debian**, только Python + один пакет.

## Требования

- Python 3.9+;
- ключ API DeepSeek;
- больше ничего: веб-поиск и разбор SRT/TXT сделаны на стандартной библиотеке.

## Установка

Общий шаг — поставить зависимость (пакет `openai`, API DeepSeek с ним совместим).

**Windows 11** (PowerShell):

```powershell
cd путь\к\tag_deepseek
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
$env:DEEPSEEK_API_KEY = "sk-..."
```

Если PowerShell запрещает активацию, выполните один раз:
`Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`.

**macOS**:

```bash
cd ~/tag_deepseek
python3 -m venv .venv && source .venv/bin/activate
python3 -m pip install -r requirements.txt
export DEEPSEEK_API_KEY=sk-...
```

**Ubuntu / Debian**:

```bash
sudo apt update && sudo apt install -y python3 python3-venv python3-pip
cd ~/tag_deepseek
python3 -m venv .venv && source .venv/bin/activate
python3 -m pip install -r requirements.txt
export DEEPSEEK_API_KEY=sk-...
```

Ключ можно не задавать в окружении, а вписать в `config.json` (поле
`api.api_key`) — но тогда не коммитьте этот файл.

## Быстрый старт

```bash
python translate.py subtitles.srt                  # китайский -> русский (по умолчанию)
python translate.py book.txt --source ko           # корейский -> русский
python translate.py interview.srt --source en      # английский -> русский
python translate.py manga.txt --source ja          # японский -> русский
python translate.py audio.txt                      # спросит режим в интерактивном меню
```

Полезные флаги:

| Флаг | Что делает |
| --- | --- |
| `-s, --source` | язык оригинала: `zh`, `ko`, `en`, `ja`, `auto` (автоопределение) |
| `-o, --output` | путь результата (по умолчанию `<имя>.<язык>.<расширение>`) |
| `-g, --glossary` | свой файл терминологии вместо `glossary/terms.tsv` |
| `--no-web` | запретить веб-поиск |
| `--batch-blocks N` | сколько блоков отправлять за один запрос (по умолчанию 40) |
| `-c, --config` | другой файл настроек |

## Режимы перевода

- При запуске без флага `--source` в терминале появляется меню: китайский,
  корейский, английский, японский или автоопределение. В скриптах (без терминала) берётся
  режим из `config.json` → `translation.source`.
- Значение по умолчанию — `zh` (китайский → русский).

## Форматы и результат

**Субтитры `.srt`** — номера и тайминги сохраняются без изменений, переводится
только текст реплик; перенос строки внутри блока сохраняется.
`movie.srt` → `movie.ru.srt`.

**Простой текст `.txt`** — строки и пустые строки сохраняются как в оригинале;
длинные строки без переносов (типично для китайского) автоматически режутся на
части и склеиваются обратно при записи. `story.txt` → `story.ru.txt`.

Формат определяется по расширению, при неясном расширении — по содержимому.

## Глоссарий

Файл `glossary/terms.tsv`, столбцы — через табуляцию:

```
zh<TAB>人工智能<TAB>искусственный интеллект<TAB>примечание
server<TAB>сервер             # краткая форма, для всех языков
```

Первый столбец — код языка (`zh`/`ko`/`en`/`ja`, `*` — любой). Агенту передаются
только те термины, которые встречаются в текущем пакете блоков. Свой файл —
флагом `-g`.

## Веб-поиск

Агенту доступны инструменты `web_search` и `fetch_url` — он вызывает их сам,
когда встречает непонятный термин, имя или реалию. Лимиты и выключение — в
`config.json` (`tools.web`) и флагом `--no-web`.

## Настройки (`config.json`)

| Ключ | Значение по умолчанию | Смысл |
| --- | --- | --- |
| `api.model` | `deepseek-flash` | имя модели |
| `api.base_url` | `https://api.deepseek.com` | адрес API |
| `api.api_key_env` | `DEEPSEEK_API_KEY` | переменная окружения с ключом |
| `translation.source` | `zh` | режим по умолчанию |
| `translation.batch_blocks` | `40` | блоков в одном запросе |
| `translation.batch_chars` | `6000` | предел символов в запросе |
| `translation.web_search` | `true` | разрешить веб-поиск |
| `glossary_file` | `glossary/terms.tsv` | файл терминологии |
| `skills_dir` | `skills` | папка навыков перевода |

Навыки (`skills/base.md`, `zh-ru.md`, `ko-ru.md`, `en-ru.md`, `ja-ru.md`, `subtitles.md`)
можно править под свой стиль и предметную область — они целиком уходят в промпт.

## Большие файлы

Файл читается генераторами, а результат пишется блок за блоком с немедленным
`flush()`. Текст никогда не накапливается в памяти целиком, частичный результат
остаётся на диске даже при прерывании (Ctrl+C). Для субтитров 300 блоков — это
≈8 запросов к модели.

## Тесты

```bash
python -m pytest -q      # не требуют ключа API и сети
```

## Структура проекта

```
translate.py              # точка входа (CLI)
config.json               # настройки
translator/
  config.py               # загрузка настроек
  langs.py                # языки и режимы
  formats.py              # разбор и потоковая запись .txt / .srt
  glossary.py             # терминология
  skills.py               # загрузка навыков
  prompts.py              # промпты для модели
  client.py               # клиент DeepSeek (повторы, ошибки)
  websearch.py            # веб-поиск и чтение страниц
  pipeline.py             # пакеты, вызов модели, потоковая запись
skills/                   # навыки перевода (base, zh-ru, ko-ru, en-ru, ja-ru, subtitles)
glossary/terms.tsv        # глоссарий
tests/                    # pytest
```
