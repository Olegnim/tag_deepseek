"""Тесты мягкой остановки перевода: флаг, исключение, обработчики сигналов."""

import signal

import pytest

from translator import stop


@pytest.fixture(autouse=True)
def clean_stop_state():
    """Флаг и обработчики не должны протекать между тестами."""
    stop.clear()
    yield
    stop.clear()
    stop.restore()


def test_check_raises_after_request():
    stop.request_stop()

    assert stop.stop_requested()
    with pytest.raises(stop.StopRequested):
        stop.check()


def test_check_is_silent_without_request():
    assert not stop.stop_requested()
    stop.check()


def test_clear_resets_flag():
    stop.request_stop()
    stop.clear()

    assert not stop.stop_requested()


def test_install_replaces_and_restore_returns_handlers():
    before_int = signal.getsignal(signal.SIGINT)
    before_term = signal.getsignal(signal.SIGTERM)

    stop.install(log=lambda *_: None)
    assert signal.getsignal(signal.SIGINT) is stop._handle
    assert signal.getsignal(signal.SIGTERM) is stop._handle

    stop.restore()
    assert signal.getsignal(signal.SIGINT) is before_int
    assert signal.getsignal(signal.SIGTERM) is before_term


def test_first_signal_requests_stop_second_interrupts():
    stop.install(log=lambda *_: None)

    stop._handle(signal.SIGINT, None)
    assert stop.stop_requested()

    # Второй сигнал не ждёт текущий запрос и возвращает обычное поведение Ctrl+C.
    with pytest.raises(KeyboardInterrupt):
        stop._handle(signal.SIGINT, None)


def test_install_is_repeatable():
    stop.install(log=lambda *_: None)
    first = signal.getsignal(signal.SIGINT)

    stop.install(log=lambda *_: None)

    assert signal.getsignal(signal.SIGINT) is first
