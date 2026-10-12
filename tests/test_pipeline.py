"""Тесты конвейера: разбор ответа модели, пакеты, потоковая запись."""

import json
import re
from io import StringIO

from translator import pipeline
from translator.formats import Segment


# ------------------------------------------------------------- заглушки API

class _Function:
    def __init__(self, name, arguments):
        self.name = name
        self.arguments = arguments


class _ToolCall:
    def __init__(self, call_id, name, arguments):
        self.id = call_id
        self.function = _Function(name, arguments)


class _Message:
    def __init__(self, content="", tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls


class _Usage:
    prompt_tokens = 10
    completion_tokens = 5


class _Response:
    def __init__(self, message):
        self.choices = [type("_Choice", (), {"message": message})()]
        self.usage = _Usage()


class FakeClient:
    """Отдаёт заранее заданные ответы и запоминает вызванные сообщения."""

    def __init__(self, responder):
        self.responder = responder
        self.calls: list[list] = []
        self.scripted: list[_Message] = []

    def chat(self, messages, tools=None):
        self.calls.append(list(messages))
        if self.scripted:
            return _Response(self.scripted.pop(0))
        return _Response(_Message(self.responder(messages)))


def echo_client():
    """Клиент, который «переводит» каждый блок как П<id>."""
    def responder(messages):
        content = messages[-1]["content"]
        ids = [int(x) for x in re.findall(r'"id":\s*(\d+)', content)]
        return json.dumps([{"id": i, "text": f"П{i}"} for i in ids], ensure_ascii=False)

    return FakeClient(responder)


def make_cfg(**overrides):
    cfg = {
        "translation": {
            "batch_blocks": 40,
            "batch_chars": 6000,
            "context_blocks": 3,
            "max_tool_steps": 4,
            "web_search": True,
        },
        "tools": {"web": {"enabled": True, "timeout": 5, "max_chars": 1000, "results": 3}},
    }
    cfg["translation"].update(overrides)
    return cfg


def make_translator(client, fmt="srt", use_web=False, **overrides):
    return pipeline.Translator(
        client=client,
        cfg=make_cfg(**overrides),
        source="zh",
        target="ru",
        skills_text="",
        terms=[],
        fmt=fmt,
        use_web=use_web,
        log=lambda *_: None,
    )


# ------------------------------------------------------------- тесты

def test_parse_translation_json_plain():
    assert pipeline.parse_translation_json('[{"id": 1, "text": "раз"}]') == {1: "раз"}


def test_parse_translation_json_with_fence():
    raw = '```json\n[{"id": 2, "text": "два"}]\n```'
    assert pipeline.parse_translation_json(raw) == {2: "два"}


def test_parse_translation_json_invalid():
    assert pipeline.parse_translation_json("никакого json") is None
    assert pipeline.parse_translation_json("") is None
    assert pipeline.parse_translation_json('{"id": 1}') is None


def test_srt_pipeline_streams_blocks():
    segments = [
        Segment(text="你好", index="1", timing="00:00:01,000 --> 00:00:02,000"),
        Segment(text="再见", index="2", timing="00:00:03,000 --> 00:00:04,000"),
    ]
    out = StringIO()
    stats = make_translator(echo_client()).run(iter(segments), out, total=2)

    assert stats.blocks == 2
    assert stats.batches == 1
    assert stats.failed == 0
    assert out.getvalue() == (
        "1\n00:00:01,000 --> 00:00:02,000\nП1\n\n"
        "2\n00:00:03,000 --> 00:00:04,000\nП2\n\n"
    )


def test_missing_translation_keeps_original():
    def responder(messages):
        return json.dumps([{"id": 1, "text": "П1"}], ensure_ascii=False)

    segments = [
        Segment(text="你好", index="1", timing="t1"),
        Segment(text="再见", index="2", timing="t2"),
    ]
    out = StringIO()
    translator = make_translator(FakeClient(responder))
    stats = translator.run(iter(segments), out, total=2)

    assert stats.failed == 1
    assert "再见" in out.getvalue()


def test_txt_passthrough_blank_line():
    segments = [
        Segment(text="hello"),
        Segment(text="", passthrough=True),
        Segment(text="world"),
    ]
    out = StringIO()
    make_translator(echo_client(), fmt="txt").run(iter(segments), out, total=2)
    assert out.getvalue() == "П1\n\nП2\n"


def test_tool_call_is_executed(monkeypatch):
    calls: list[str] = []

    def fake_search(query, max_results=5, timeout=20):
        calls.append(query)
        return "результаты поиска"

    monkeypatch.setattr(pipeline, "web_search", fake_search)

    tool_message = _Message(
        content="",
        tool_calls=[_ToolCall("1", "web_search", json.dumps({"query": "термин"}))],
    )
    client = FakeClient(lambda messages: "[]")
    client.scripted = [
        tool_message,
        _Message(content=json.dumps([{"id": 1, "text": "П1"}], ensure_ascii=False)),
    ]

    segments = [Segment(text="你好", index="1", timing="t1")]
    out = StringIO()
    translator = make_translator(client, use_web=True)
    stats = translator.run(iter(segments), out, total=1)

    assert calls == ["термин"]
    assert stats.tool_calls == 1
    assert "П1" in out.getvalue()


def test_batches_split_by_block_count():
    segments = [Segment(text=f"строка {i}") for i in range(1, 6)]
    out = StringIO()
    stats = make_translator(echo_client(), fmt="txt", batch_blocks=2).run(
        iter(segments), out, total=5
    )
    assert stats.batches == 3
    assert stats.blocks == 5
