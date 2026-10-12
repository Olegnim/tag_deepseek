"""Живой прогресс перевода в терминале.

На одной обновляемой строке видно: бар с долей готовых блоков, что делает агент
прямо сейчас («⠹ модель думает 12 с», «⠋ ищу: 紫微斗数»), общее время и счётчики
(пакеты, веб-запросы, токены).

Индикатор текущего действия виден всегда: спиннер крутится, пока переводится
файл, а секунды считаются от начала фазы — по ним понятно, что агент не завис.
Общее время уходит с экрана, пока совпадает со временем фазы. Строка подгоняется
под ширину терминала: сначала уходят счётчики, затем имя файла и время; бар, доля
блоков и описание действия не пропадают.

Терминал можно сузить в любой момент — тогда уже нарисованная строка «переедет»
на несколько строк. Поэтому перед каждой перерисовкой стирается весь блок
строк, занятый предыдущим выводом (курсор поднимается к его началу и экран
очищается до конца), и мусора на экране не остаётся.

Сообщения и ошибки печатаются над строкой состояния, чтобы не ломать бар.
Если вывод не терминал (файл, конвейер) или прогресс отключён флагом, класс
работает как обычный логгер: по одной строке на событие, без анимации.
"""

from __future__ import annotations

import os
import shutil
import sys
import threading
import time
import unicodedata

_SPINNER = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")
_BAR_MIN = 10
_BAR_MAX = 24
# Сколько колонок остаётся остальным частям строки после бара.
_BAR_RESERVE = 45
_SPIN_INTERVAL = 0.15
_MIN_TITLE = 8
_MAX_TITLE = 40
# Сколько колонок нужно, чтобы показать описание действия (спиннер + текст).
_MIN_ACTIVITY = 4
_MIN_WIDTH = 40
_FALLBACK_WIDTH = 80
# Через сколько секунд после старта общее время перестаёт дублировать время фазы.
_TIME_DUP = 3.0
# Порядок частей строки состояния.
_SLOT_ORDER = ("title", "bar", "blocks", "activity", "time", "tokens", "web", "batches")


def human_int(value: int) -> str:
    """12345 -> «12 345»: разделитель тысяч — узкий пробел."""
    return f"{int(value):,}".replace(",", " ")


def human_seconds(value: float) -> str:
    """Длительность: «3.4 с», «47 с» или «2 мин 05 с»."""
    if value < 10:
        return f"{value:.1f} с"
    if value < 60:
        return f"{value:.0f} с"
    minutes, seconds = divmod(int(value), 60)
    return f"{minutes} мин {seconds:02d} с"


def char_width(char: str) -> int:
    """Сколько колонок терминала занимает символ (иероглиф — две, диакритика — ноль)."""
    if unicodedata.combining(char):
        return 0
    return 2 if unicodedata.east_asian_width(char) in ("W", "F") else 1


def display_width(text: str) -> int:
    """Ширина строки в колонках терминала."""
    return sum(char_width(char) for char in text)


def clip_to(text: str, limit: int) -> str:
    """Обрезает строку до заданной ширины, отмечая обрезку многоточием."""
    if limit <= 0:
        return ""
    if display_width(text) <= limit:
        return text
    if limit == 1:
        return "…"
    kept: list[str] = []
    used = 0
    for char in text:
        size = char_width(char)
        if used + size > limit - 1:
            break
        kept.append(char)
        used += size
    return "".join(kept) + "…"


def bar_width(limit: int) -> int:
    """Ширина бара: на узком терминале он короче, чтобы влезли остальные части."""
    return max(_BAR_MIN, min(_BAR_MAX, limit - _BAR_RESERVE))


class Progress:
    """Одна обновляемая строка статуса + обычный журнал над ней."""

    def __init__(self, stream=None, enabled: bool = True, log=None) -> None:
        self.stream = stream if stream is not None else sys.stdout
        self.log = log if log is not None else (
            lambda message: print(message, file=self.stream)
        )
        is_tty = bool(getattr(self.stream, "isatty", lambda: False)())
        self.enabled = bool(enabled and is_tty)

        self.total = 0
        self.blocks = 0
        self.batches = 0
        self.tool_calls = 0
        self.tokens = 0
        self.started: float | None = None
        self.title = ""

        self._label = "подготовка"
        self._phase_since: float | None = None
        self._frame = 0
        self._last_width = 0
        self._lock = threading.RLock()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    # ------------------------------------------------------------ lifecycle

    def start(self, total: int = 0, title: str = "") -> None:
        """Начинает новый отсчёт (для одного файла)."""
        self._stop_spinner()
        self.total = max(0, int(total))
        self.blocks = 0
        self.batches = 0
        self.tool_calls = 0
        self.tokens = 0
        self.started = time.time()
        self.title = title
        self._last_width = 0
        self._set_phase("подготовка")
        self._start_spinner()
        self._render()

    def finish(self) -> None:
        """Завершает строку состояния: дописывает итог и переводит курсор."""
        self._stop_spinner()
        with self._lock:
            if self.enabled and self.started is not None:
                line = self._line()
                self._erase()
                self.stream.write(line + "\n")
                self.stream.flush()
            self._last_width = 0
        self.started = None

    def stop(self) -> None:
        """Убирает строку состояния, ничего не печатая (ошибка, прерывание)."""
        self._stop_spinner()
        with self._lock:
            if self.enabled and self.started is not None:
                self._erase()
                self.stream.flush()
            self._last_width = 0
        self.started = None

    # ------------------------------------------------------------ события

    def update(
        self,
        blocks: int | None = None,
        batches: int | None = None,
        tool_calls: int | None = None,
        tokens: int | None = None,
    ) -> None:
        """Обновляет счётчики и перерисовывает строку состояния."""
        if blocks is not None:
            self.blocks = int(blocks)
        if batches is not None:
            self.batches = int(batches)
        if tool_calls is not None:
            self.tool_calls = int(tool_calls)
        if tokens is not None:
            self.tokens = int(tokens)
        self._render()

    def batch_started(self, index: int, first: int, last: int, total: int = 0) -> None:
        """Отмечает начало нового пакета блоков."""
        where = f"{first}-{last}" + (f" из {total}" if total else "")
        self._set_phase(f"пакет {index}: блоки {where}")
        if self.enabled:
            self._render()
            return
        details = []
        if self.tokens:
            details.append(f"токенов {human_int(self.tokens)}")
        if self.started is not None:
            details.append(f"прошло {human_seconds(self._elapsed())}")
        tail = (" · " + " · ".join(details)) if details else ""
        self.log(f"[пакет {index}] блоки {where}{tail}")

    def request_started(self, label: str = "модель думает") -> None:
        """Показывает, что запрос ушёл в модель и ответ ещё не пришёл."""
        self._set_phase(label)
        self._render()

    def request_finished(self) -> None:
        """Меняет фазу: ответ получен, идёт разбор и запись результата."""
        self._set_phase("обрабатываю ответ")
        self._render()

    def phase(self, label: str) -> None:
        """Другое текущее действие агента (например, «ищу: 紫微斗数»)."""
        self._set_phase(label)
        self._render()

    def note(self, message: str) -> None:
        """Строка журнала: печатается над строкой состояния."""
        with self._lock:
            if self.enabled and self.started is not None:
                self._erase()
                self.stream.write(message + "\n")
                self.stream.flush()
                self._render()
            else:
                self.log(message)

    def warning(self, message: str) -> None:
        """Проблема: пользователь должен увидеть её даже при активном баре."""
        self.note(message)

    # ------------------------------------------------------------ отрисовка

    def _set_phase(self, label: str) -> None:
        """Ставит текущее действие и обнуляет счётчик секунд в фазе."""
        self._label = label
        self._phase_since = time.time()

    def _start_spinner(self) -> None:
        self._stop_spinner()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._spin, daemon=True)
        self._thread.start()

    def _spin(self) -> None:
        while not self._stop.wait(_SPIN_INTERVAL):
            self._frame += 1
            self._render()

    def _stop_spinner(self) -> None:
        thread = self._thread
        if thread is None:
            return
        self._stop.set()
        thread.join(timeout=1.0)
        self._thread = None

    def _elapsed(self) -> float:
        return time.time() - self.started if self.started else 0.0

    def _columns(self) -> int:
        """Ширина терминала в колонках."""
        columns = 0
        try:
            columns = os.get_terminal_size(self.stream.fileno()).columns
        except (AttributeError, ValueError, OSError):
            columns = 0
        if columns <= 0:
            columns = shutil.get_terminal_size(fallback=(_FALLBACK_WIDTH, 24)).columns
        if columns < _MIN_WIDTH:
            columns = _FALLBACK_WIDTH
        return columns

    def _width(self) -> int:
        """Сколько колонок можно занять строкой состояния (без переноса)."""
        # Последнюю колонку не занимаем: на ней терминал может перенести строку.
        return self._columns() - 1

    def _rows(self, width: int) -> int:
        """Сколько строк экрана занимает текст такой ширины."""
        if width <= 0:
            return 0
        columns = self._columns()
        return max(1, (width + columns - 1) // columns)

    def _erase(self) -> None:
        """Стирает строку состояния и строки, на которые её перенёс терминал."""
        if self._last_width <= 0:
            return
        rows = self._rows(self._last_width)
        if rows > 1:
            self.stream.write(f"\r\033[{rows - 1}A\r\033[J")
        else:
            self.stream.write("\r\033[J")
        self._last_width = 0

    def _bar_part(self, limit: int) -> str:
        """Бар и доля готовых блоков — обязательная часть строки."""
        if not self.total:
            return f"{self.blocks} блоков"
        ratio = min(1.0, self.blocks / self.total)
        width = bar_width(limit)
        filled = int(round(width * ratio))
        bar = "█" * filled + "░" * (width - filled)
        return f"{bar} {ratio * 100:5.1f}%"

    def _blocks_part(self) -> str:
        return f"{self.blocks}/{self.total} блоков" if self.total else f"{self.blocks} блоков"

    def _time_part(self) -> str:
        """Общее время перевода.

        Пока оно почти совпадает со временем текущей фазы (например, сразу
        после старта), не показываем его — иначе на экране две одинаковые
        цифры подряд.
        """
        if (self.started is None or self._phase_since is None
                or self._phase_since - self.started < _TIME_DUP):
            return ""
        return human_seconds(self._elapsed())

    def _line(self) -> str:
        """Строка состояния, ужатая до ширины терминала.

        Обязательное — бар с долей блоков и текущее действие (там же спиннер).
        Дальше по важности добавляем счётчик блоков, время, имя файла и
        счётчики; что не влезло, не показываем — иначе строка переносится и
        бар «размазывается».
        """
        limit = self._width()
        slots: dict[str, str] = {"bar": self._bar_part(limit)}
        if display_width(self._assemble(slots)) >= limit:
            # Терминал совсем узкий: обрезаем, чтобы строка не переносилась.
            return clip_to(self._assemble(slots), limit)

        frame, waited, label = self._activity_parts()
        activity = self._activity(frame, waited, label)
        room = self._room(slots, limit)
        if activity and room >= _MIN_ACTIVITY:
            slots["activity"] = clip_to(activity, room)

        for name, text in (
            ("blocks", self._blocks_part()),
            ("time", self._time_part()),
        ):
            if display_width(text) > self._room(slots, limit):
                continue
            slots[name] = text

        if self.title:
            room = min(_MAX_TITLE, self._room(slots, limit))
            if room >= _MIN_TITLE:
                slots["title"] = clip_to(self.title, room)

        for name, text in (
            ("tokens", f"токенов {human_int(self.tokens)}" if self.tokens else ""),
            ("web", f"веб-запросов {self.tool_calls}" if self.tool_calls else ""),
            ("batches", f"пакетов {self.batches}" if self.batches else ""),
        ):
            if not text or display_width(text) > self._room(slots, limit):
                continue
            slots[name] = text

        line = self._assemble(slots)
        return line if display_width(line) <= limit else clip_to(line, limit)

    def _room(self, slots: dict[str, str], limit: int) -> int:
        """Сколько колонок осталось под ещё одну часть строки."""
        return limit - display_width(self._assemble(slots)) - 2

    def _assemble(self, slots: dict[str, str]) -> str:
        """Собирает строку из частей в порядке показа."""
        return "  ".join(slots[name] for name in _SLOT_ORDER if slots.get(name))

    def _activity_parts(self) -> tuple[str, str, str]:
        """Спиннер, секунды в текущей фазе и её описание."""
        frame = _SPINNER[self._frame % len(_SPINNER)]
        waited = ""
        if self._phase_since is not None:
            waited = human_seconds(time.time() - self._phase_since)
        return frame, waited, self._label

    @staticmethod
    def _activity(frame: str = "", waited: str = "", label: str = "") -> str:
        """Блок активности: «⠙ модель думает 7 с»."""
        if not frame:
            return label
        return f"{frame} {label} {waited}" if label else f"{frame} {waited}"

    def _render(self) -> None:
        if not self.enabled or self.started is None:
            return
        line = self._line()
        with self._lock:
            self._erase()
            self.stream.write(line)
            self.stream.flush()
            self._last_width = display_width(line)
