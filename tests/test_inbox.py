"""Тесты папок input/output и сквозного режима «положил файл — получил перевод»."""

import json
import re
import sys
from pathlib import Path

import pytest

import translate
from translator import inbox
from translator.client import ApiError

SRT_ONE_BLOCK = "1\n00:00:01,000 --> 00:00:02,000\n你好\n\n"


# ------------------------------------------------------------- заглушка API

class _Message:
    def __init__(self, content):
        self.content = content
        self.tool_calls = None


class _Response:
    def __init__(self, content):
        self.choices = [type("_Choice", (), {"message": _Message(content)})()]
        self.usage = type("_Usage", (), {"prompt_tokens": 10, "completion_tokens": 5})()


class _FakeClient:
    """Отвечает переводом П<id> или падает с ошибкой API."""

    def __init__(self, cfg=None, log=print, fail=False):
        self.fail = fail

    def chat(self, messages, tools=None):
        if self.fail:
            raise ApiError("сеть недоступна")
        content = messages[-1]["content"]
        ids = [int(x) for x in re.findall(r'"id":\s*(\d+)', content)]
        return _Response(json.dumps([{"id": i, "text": f"П{i}"} for i in ids], ensure_ascii=False))


@pytest.fixture
def dirs(tmp_path):
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    input_dir.mkdir()
    output_dir.mkdir()
    return input_dir, output_dir


def run_main(monkeypatch, argv: list[str], fail: bool = False) -> int:
    monkeypatch.setattr(sys, "argv", ["translate.py", *argv])
    monkeypatch.setattr(translate, "DeepSeekClient", lambda cfg, log=print: _FakeClient(fail=fail))
    return translate.main()


# ------------------------------------------------------------- inbox

def test_ensure_dir_creates_nested(tmp_path):
    target = tmp_path / "a" / "b"
    ok, error = inbox.ensure_dir(target)
    assert ok and error == ""
    assert target.is_dir()


def test_ensure_dir_reports_file_instead_of_dir(tmp_path):
    file_path = tmp_path / "file.txt"
    file_path.write_text("x", encoding="utf-8")
    ok, error = inbox.ensure_dir(file_path)
    assert not ok
    assert "не папка" in error


def test_list_sources_splits_by_format(tmp_path):
    (tmp_path / "a.srt").write_text("x", encoding="utf-8")
    (tmp_path / "b.txt").write_text("x", encoding="utf-8")
    (tmp_path / "c.pdf").write_text("x", encoding="utf-8")
    (tmp_path / ".DS_Store").write_text("x", encoding="utf-8")
    (tmp_path / ".hidden.srt").write_text("x", encoding="utf-8")

    supported, other = inbox.list_sources(tmp_path)
    assert [p.name for p in supported] == ["a.srt", "b.txt"]
    assert [p.name for p in other] == ["c.pdf"]


def test_unique_path_does_not_overwrite(tmp_path):
    (tmp_path / "movie.ru.srt").write_text("x", encoding="utf-8")
    path = inbox.unique_path(tmp_path, "movie.ru.srt")
    assert path.name == "movie.ru-2.srt"


def test_human_size():
    assert inbox.human_size(512) == "512 Б"
    assert inbox.human_size(-1) == "размер неизвестен"
    assert "КБ" in inbox.human_size(2048)


def test_clean_input_keeps_file_when_disabled(tmp_path):
    path = tmp_path / "a.txt"
    path.write_text("x", encoding="utf-8")
    ok, _ = inbox.clean_input(path, tmp_path / "out.txt", clear=False)
    assert ok and path.exists()


# ------------------------------------------------------------- сквозной режим

def test_inbox_flow_translates_and_clears_input(monkeypatch, dirs, capsys):
    input_dir, output_dir = dirs
    source = input_dir / "movie.srt"
    source.write_text(SRT_ONE_BLOCK, encoding="utf-8")

    code = run_main(monkeypatch, [
        "--input-dir", str(input_dir), "--output-dir", str(output_dir),
        "--source", "zh", "--no-web",
    ])
    capsys.readouterr()

    assert code == 0
    result = output_dir / "movie.ru.srt"
    assert result.is_file()
    assert "П1" in result.read_text(encoding="utf-8")
    assert not source.exists(), "исходник должен быть убран из input"
    assert list(input_dir.iterdir()) == []


def test_inbox_flow_keeps_file_on_api_error(monkeypatch, dirs, capsys):
    input_dir, output_dir = dirs
    source = input_dir / "movie.srt"
    source.write_text(SRT_ONE_BLOCK, encoding="utf-8")

    code = run_main(monkeypatch, [
        "--input-dir", str(input_dir), "--output-dir", str(output_dir),
        "--source", "zh", "--no-web",
    ], fail=True)
    output = capsys.readouterr().out

    assert code == 4
    assert source.exists(), "при ошибке исходник остаётся на месте"
    assert "ошибка API" in output
    assert not (output_dir / "movie.ru.srt").exists()


def test_empty_inbox_is_not_an_error(monkeypatch, dirs, capsys):
    input_dir, output_dir = dirs
    code = run_main(monkeypatch, [
        "--input-dir", str(input_dir), "--output-dir", str(output_dir),
    ])
    output = capsys.readouterr().out
    assert code == 0
    assert "нет файлов для перевода" in output


def test_missing_explicit_file(monkeypatch, dirs, capsys):
    input_dir, output_dir = dirs
    code = run_main(monkeypatch, [
        str(input_dir / "nope.srt"),
        "--output-dir", str(output_dir),
    ])
    assert code == 2
    capsys.readouterr()


def test_explicit_file_keeps_source(monkeypatch, dirs, capsys):
    input_dir, output_dir = dirs
    source = input_dir / "text.txt"
    source.write_text("你好。\n", encoding="utf-8")

    code = run_main(monkeypatch, [
        str(source), "--output-dir", str(output_dir), "--source", "zh", "--no-web",
    ])
    capsys.readouterr()

    assert code == 0
    assert source.exists(), "файл, переданный явно, не удаляем"
    assert (output_dir / "text.ru.txt").is_file()
