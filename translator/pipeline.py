"""Конвейер перевода: блоки → пакеты → DeepSeek → потоковая запись в файл.

Ключевые свойства:
  * результат пишется «блок за блоком» с flush после каждого блока — файл
    можно смотреть прямо во время перевода, память не растёт;
  * модели разрешено вызывать web_search/fetch_url, чтобы уточнять термины;
  * если модель вернула неполный JSON, недостающие блоки переводятся отдельно.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Iterator

from . import formats, prompts
from .client import ApiError, DeepSeekClient
from .glossary import Term, select_terms
from .progress import Progress
from .websearch import fetch_url, web_search

TOOL_SPECS = [
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": (
                "Поиск в интернете. Используй, чтобы уточнить значение термина, "
                "имя собственное, реалию или факт перед переводом."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Поисковый запрос"}
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fetch_url",
            "description": "Прочитать текст веб-страницы по URL для проверки фактов и терминологии.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "Полный http(s)-адрес"}
                },
                "required": ["url"],
            },
        },
    },
]


@dataclass
class Stats:
    blocks: int = 0
    written: int = 0
    batches: int = 0
    failed: int = 0
    tool_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    started: float = field(default_factory=time.time)

    @property
    def elapsed(self) -> float:
        return time.time() - self.started

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


def parse_translation_json(content: str) -> dict[int, str] | None:
    """Разбирает ответ модели в {id: перевод}. None — если разобрать не удалось."""
    if not content:
        return None
    text = content.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end == -1 or end < start:
        return None
    try:
        data = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None
    if not isinstance(data, list):
        return None
    result: dict[int, str] = {}
    for item in data:
        if not isinstance(item, dict):
            continue
        raw_id = item.get("id")
        raw_text = item.get("text")
        if raw_id is None or raw_text is None:
            continue
        try:
            key = int(raw_id)
        except (TypeError, ValueError):
            continue
        result[key] = str(raw_text)
    return result or None


class Translator:
    def __init__(
        self,
        client: DeepSeekClient,
        cfg: dict,
        source: str,
        target: str,
        skills_text: str,
        terms: list[Term],
        fmt: str,
        use_web: bool = True,
        log=print,
        progress: Progress | None = None,
    ) -> None:
        translation = cfg["translation"]
        web = cfg.get("tools", {}).get("web", {})
        self.client = client
        self.source = source
        self.target = target
        self.skills = skills_text
        self.terms = terms
        self.fmt = fmt
        self.use_web = use_web and bool(web.get("enabled", True))
        self.batch_blocks = max(1, int(translation.get("batch_blocks", 40)))
        self.batch_chars = max(200, int(translation.get("batch_chars", 6000)))
        self.context_blocks = max(0, int(translation.get("context_blocks", 3)))
        self.max_tool_steps = max(0, int(translation.get("max_tool_steps", 4)))
        self.web_timeout = float(web.get("timeout", 20))
        self.web_max_chars = int(web.get("max_chars", 12000))
        self.web_results = int(web.get("results", 5))
        self.log = log
        self.progress = progress if progress is not None else Progress(enabled=False, log=log)
        self.recent: list[str] = []

    # ------------------------------------------------------------ запуск

    def run(self, segments: Iterator[formats.Segment], out_fh, total: int = 0) -> Stats:
        stats = Stats()
        batch: list[formats.Segment] = []
        api_blocks = 0
        api_chars = 0

        def flush() -> None:
            nonlocal batch, api_blocks, api_chars
            if not batch:
                return
            self._process_batch(batch, out_fh, stats, total)
            batch = []
            api_blocks = 0
            api_chars = 0

        for segment in segments:
            if segment.passthrough:
                batch.append(segment)
                continue
            stats.blocks += 1
            segment.seq = stats.blocks
            batch.append(segment)
            api_blocks += 1
            api_chars += len(segment.text)
            if api_blocks >= self.batch_blocks or api_chars >= self.batch_chars:
                flush()

        flush()
        return stats

    # ------------------------------------------------------------ пакет

    def _process_batch(self, batch: list[formats.Segment], out_fh, stats: Stats, total: int) -> None:
        api_segments = [s for s in batch if not s.passthrough]
        if api_segments:
            stats.batches += 1
            first = api_segments[0].seq
            last = api_segments[-1].seq
            self.progress.batch_started(stats.batches, first, last, total)
            translations = self._translate(api_segments, stats)
            for segment in api_segments:
                if segment.seq in translations:
                    segment.translated = translations[segment.seq]
                else:
                    segment.translated = segment.text
                    stats.failed += 1
                    self.progress.warning(
                        f"  ! блок {segment.seq}: перевод не получен, оставлен оригинал"
                    )
            if self.context_blocks:
                self.recent = [s.translated for s in api_segments][-self.context_blocks:]

        for segment in batch:
            text = segment.text if segment.passthrough else segment.translated
            formats.write_segment(out_fh, self.fmt, segment, text)
            out_fh.flush()
            if not segment.passthrough:
                stats.written += 1
        self._sync(stats)

    # ------------------------------------------------------------ модель

    def _translate(self, segments: list[formats.Segment], stats: Stats) -> dict[int, str]:
        messages = self._messages(segments)
        tools = TOOL_SPECS if self.use_web else None
        attempts = self.max_tool_steps + 2
        after_tools = False

        for _ in range(attempts):
            label = "модель думает после веб-поиска" if after_tools else "модель думает"
            response = self._chat(messages, tools, label=label)
            after_tools = False
            self._count_usage(response, stats)
            self._sync(stats)
            message = response.choices[0].message
            tool_calls = getattr(message, "tool_calls", None)
            if tool_calls:
                messages.append(message)
                for call in tool_calls:
                    result = self._run_tool(call, stats)
                    messages.append(
                        {"role": "tool", "tool_call_id": call.id, "content": result}
                    )
                after_tools = True
                self._sync(stats)
                continue
            parsed = parse_translation_json(message.content or "")
            if parsed is not None:
                return parsed
            messages.append(message)
            messages.append(
                {
                    "role": "user",
                    "content": (
                        "Ответ не является корректным JSON-массивом. Верни только JSON "
                        f"вида {prompts.JSON_SHAPE} и ничего больше."
                    ),
                }
            )

        self.progress.warning("  ! пакет не удалось разобрать, перевожу блоки по одному")
        return self._translate_one_by_one(segments, stats)

    def _translate_one_by_one(self, segments: list[formats.Segment], stats: Stats) -> dict[int, str]:
        result: dict[int, str] = {}
        for segment in segments:
            try:
                response = self._chat(self._messages([segment]), None, label="перевод по одному блоку")
            except ApiError as exc:
                self.progress.warning(f"  ! блок {segment.seq}: {exc}")
                continue
            self._count_usage(response, stats)
            self._sync(stats)
            content = (response.choices[0].message.content or "").strip()
            parsed = parse_translation_json(content)
            if parsed and segment.seq in parsed:
                result[segment.seq] = parsed[segment.seq]
            elif content:
                result[segment.seq] = content
        return result

    def _messages(self, segments: list[formats.Segment]) -> list[dict]:
        text = "\n".join(s.text for s in segments)
        terms = select_terms(self.terms, self.source, text)
        return prompts.build_messages(
            source=self.source,
            target=self.target,
            skills=self.skills,
            terms=terms,
            fmt=self.fmt,
            recent=self.recent if self.context_blocks else [],
            segments=segments,
            use_web=self.use_web,
        )

    def _chat(self, messages: list, tools: list | None, label: str = "модель думает"):
        """Запрос к модели с индикатором ожидания в строке прогресса."""
        self.progress.request_started(label)
        try:
            return self.client.chat(messages, tools=tools)
        finally:
            self.progress.request_finished()

    def _sync(self, stats: Stats) -> None:
        """Переносит счётчики статистики в строку прогресса."""
        self.progress.update(
            blocks=stats.written,
            batches=stats.batches,
            tool_calls=stats.tool_calls,
            tokens=stats.total_tokens,
        )

    def _run_tool(self, call, stats: Stats) -> str:
        name = call.function.name
        try:
            args = json.loads(call.function.arguments or "{}")
        except json.JSONDecodeError:
            args = {}
        stats.tool_calls += 1
        try:
            if name == "web_search":
                query = str(args.get("query", ""))
                self.progress.phase(f"ищу: {query}")
                self.progress.note(f"  · поиск: {query}")
                return web_search(query, max_results=self.web_results, timeout=self.web_timeout)
            if name == "fetch_url":
                url = str(args.get("url", ""))
                self.progress.phase("читаю страницу")
                self.progress.note(f"  · страница: {url}")
                return fetch_url(url, max_chars=self.web_max_chars, timeout=self.web_timeout)
        except Exception as exc:  # noqa: BLE001 — инструмент не должен ломать перевод
            self.progress.warning(f"  ! {name} не сработал: {exc}")
            return f"Инструмент {name} завершился ошибкой: {exc}"
        return f"Неизвестный инструмент: {name}"

    @staticmethod
    def _count_usage(response, stats: Stats) -> None:
        usage = getattr(response, "usage", None)
        if usage is None:
            return
        stats.prompt_tokens += int(getattr(usage, "prompt_tokens", 0) or 0)
        stats.completion_tokens += int(getattr(usage, "completion_tokens", 0) or 0)
