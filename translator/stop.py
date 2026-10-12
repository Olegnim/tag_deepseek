"""Мягкая остановка перевода: Ctrl+C и SIGTERM завершают работу аккуратно.

Первый сигнал только *просит* остановиться: текущий запрос к модели доводится
до конца, готовый пакет дописывается на диск, а остановка происходит на
ближайшей безопасной границе — перед следующим пакетом блоков. Второй сигнал
прерывает работу сразу (привычное поведение Ctrl+C).

Так не остаётся «фоновых» потоков и недописанных файлов: строка прогресса
гасится, частичный перевод уже лежит на диске, исходник в input/ не трогается.

Флаг остановки один на процесс (`threading.Event`), его выставляет обработчик
сигнала, а конвейер лишь проверяет его на безопасных границах (`check()`).
"""

from __future__ import annotations

import signal
import threading
from typing import Callable

# Сигналы, которые трактуем как просьбу остановиться.
_SIGNALS = (signal.SIGINT, signal.SIGTERM)

_stop = threading.Event()
_log: Callable[[str], None] | None = None
_installed: dict[int, object] = {}


class StopRequested(Exception):
    """Получен сигнал остановки: работу нужно свернуть на безопасной границе."""


def request_stop() -> None:
    """Просит остановиться (используется обработчиком сигнала и тестами)."""
    _stop.set()


def stop_requested() -> bool:
    """True, если пришёл сигнал остановки."""
    return _stop.is_set()


def clear() -> None:
    """Сбрасывает флаг остановки."""
    _stop.clear()


def check() -> None:
    """Бросает StopRequested, если пришёл сигнал остановки."""
    if _stop.is_set():
        raise StopRequested("остановлено пользователем")


def set_logger(log: Callable[[str], None] | None) -> None:
    """Задаёт функцию для сообщений об остановке (обычно журнал прогресса)."""
    global _log
    _log = log


def _handle(signum, frame) -> None:
    if _stop.is_set():
        # Второй сигнал: не ждём текущий запрос к модели.
        restore()
        raise KeyboardInterrupt
    _stop.set()
    if _log is not None:
        _log(
            "Останавливаю перевод: текущий пакет допишется и программа выйдет "
            "(Ctrl+C ещё раз — прервать сразу)."
        )


def install(log: Callable[[str], None] | None = None) -> None:
    """Ставит обработчики SIGINT/SIGTERM.

    Вне главного потока сигналы установить нельзя — тогда функция молчит, и
    остановку можно запросить вручную через request_stop().
    """
    global _log
    _log = log
    _stop.clear()
    if threading.current_thread() is not threading.main_thread():
        return
    for signum in _SIGNALS:
        if signum in _installed:
            continue
        try:
            _installed[signum] = signal.signal(signum, _handle)
        except (ValueError, OSError):  # сигнал недоступен на этой платформе
            pass


def restore() -> None:
    """Возвращает прежние обработчики сигналов."""
    global _log
    _log = None
    for signum, previous in list(_installed.items()):
        try:
            signal.signal(signum, previous)
        except (ValueError, OSError):
            pass
    _installed.clear()
