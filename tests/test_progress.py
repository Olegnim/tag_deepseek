"""Тесты строки прогресса: бар, счётчики, режим «не терминал»."""

from io import StringIO

from translator.progress import Progress, clip_to, display_width, human_int


class TTY(StringIO):
    """Поток, притворяющийся терминалом."""

    def isatty(self) -> bool:
        return True


def test_human_int_groups_thousands():
    assert human_int(0) == "0"
    assert human_int(1234567) == "1 234 567"


def test_cjk_counts_as_two_columns():
    assert display_width("abc") == 3
    assert display_width("中文") == 4
    assert display_width(clip_to("中文名字", 5)) == 5
    assert clip_to("abcdef", 4) == "abc…"


def test_disabled_progress_logs_plain_lines():
    lines: list[str] = []
    progress = Progress(stream=StringIO(), enabled=True, log=lines.append)

    progress.start(total=10)
    progress.update(blocks=5, tokens=1000)
    progress.batch_started(2, 41, 80, total=80)

    assert len(lines) == 1
    assert lines[0].startswith("[пакет 2] блоки 41-80 из 80")
    assert "токенов 1 000" in lines[0]


def test_enabled_progress_draws_bar_with_tokens(monkeypatch):
    monkeypatch.setenv("COLUMNS", "120")
    stream = TTY()
    progress = Progress(stream=stream, enabled=True)
    progress.start(total=10, title="movie.srt")
    progress.update(blocks=5, batches=1, tokens=1234)
    progress.finish()
    out = stream.getvalue()

    assert "50.0%" in out
    assert "5/10 блоков" in out
    assert "токенов 1 234" in out
    assert out.endswith("\n")


def test_spinner_runs_while_file_is_translated(monkeypatch):
    """Спиннер видно всегда, а не только во время запроса к модели."""
    monkeypatch.setenv("COLUMNS", "120")
    stream = TTY()
    progress = Progress(stream=stream, enabled=True)
    progress.start(total=1)
    assert progress._thread is not None

    progress.request_started("модель думает")
    progress.request_finished()
    assert progress._thread is not None
    assert "модель думает" in stream.getvalue()

    progress.finish()
    assert progress._thread is None


def test_narrow_terminal_keeps_activity_and_bar(monkeypatch):
    """Даже на узком терминале видно, что модель думает."""
    monkeypatch.setenv("COLUMNS", "60")
    stream = TTY()
    progress = Progress(stream=stream, enabled=True)
    progress.start(total=53, title="20261002.txt")
    progress.batch_started(1, 1, 53, 53)
    progress.request_started("модель думает")
    line = progress._line()
    progress.finish()

    assert display_width(line) <= 59
    assert "0/53 блоков" in line
    assert "модель думает" in line
    assert "░" in line


def test_resize_erases_rows_used_by_previous_line(monkeypatch):
    """При сужении окна старая строка занимала несколько строк — их стираем."""
    monkeypatch.setenv("COLUMNS", "120")
    stream = TTY()
    progress = Progress(stream=stream, enabled=True)
    progress.start(total=53, title="20261002.txt")
    progress.update(blocks=20, batches=1, tool_calls=1, tokens=12345)
    old_width = progress._last_width
    assert progress._rows(old_width) == 1

    monkeypatch.setenv("COLUMNS", "50")
    stream.seek(0)
    stream.truncate(0)
    progress.update(blocks=21)
    out = stream.getvalue()
    rows = progress._rows(old_width)

    assert rows > 1
    assert f"\033[{rows - 1}A" in out  # курсор поднят к началу блока
    assert out.endswith(progress._line())


def test_status_line_fits_terminal_width(monkeypatch):
    """Длинная строка ужимается: перенос ломает бар на экране."""
    monkeypatch.setenv("COLUMNS", "100")
    stream = TTY()
    progress = Progress(stream=stream, enabled=True)
    progress.start(total=53, title="20261002.txt")
    progress.batch_started(1, 1, 53, 53)
    progress.update(blocks=0, batches=1, tool_calls=4, tokens=9367)
    progress.request_started("модель думает после веб-поиска")
    line = progress._line()
    progress.request_finished()

    assert display_width(line) <= 99
    assert "0/53 блоков" in line
    assert "модель думает" in line


def test_narrow_terminal_drops_counters_but_keeps_bar(monkeypatch):
    monkeypatch.setenv("COLUMNS", "60")
    stream = TTY()
    progress = Progress(stream=stream, enabled=True)
    progress.start(total=53, title="20261002.txt")
    progress.update(blocks=26, batches=1, tool_calls=4, tokens=9367)
    line = progress._line()

    assert display_width(line) <= 59
    assert "26/53 блоков" in line
    assert "токенов" not in line
    assert "веб-запросов" not in line


def test_phase_label_is_shown_in_status_line(monkeypatch):
    monkeypatch.setenv("COLUMNS", "120")
    stream = TTY()
    progress = Progress(stream=stream, enabled=True)
    progress.start(total=10)
    progress.phase("ищу: 紫微斗数")
    line = progress._line()
    progress.finish()

    assert "ищу: 紫微斗数" in line
    assert display_width(line) <= 119


def test_note_prints_above_bar():
    stream = TTY()
    progress = Progress(stream=stream, enabled=True)
    progress.start(total=4)
    progress.note("  ! сбой API (RateLimitError)")

    assert "! сбой API" in stream.getvalue()


def test_stop_after_error_leaves_no_bar():
    stream = TTY()
    progress = Progress(stream=stream, enabled=True)
    progress.start(total=4)
    progress.stop()

    assert progress.started is None
    assert not stream.getvalue().endswith("блоков")
