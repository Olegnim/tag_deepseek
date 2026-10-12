"""Тесты разбора и записи форматов."""

from io import StringIO

from translator import formats


def test_detect_format_by_suffix(tmp_path):
    srt = tmp_path / "a.srt"
    srt.write_text("1\n00:00:01,000 --> 00:00:02,000\nhi\n", encoding="utf-8")
    assert formats.detect_format(srt) == "srt"

    txt = tmp_path / "a.txt"
    txt.write_text("привет", encoding="utf-8")
    assert formats.detect_format(txt) == "txt"


def test_detect_format_by_content(tmp_path):
    path = tmp_path / "unknown.dat"
    path.write_text("1\n00:00:01,000 --> 00:00:02,000\nhi\n", encoding="utf-8")
    assert formats.detect_format(path) == "srt"


def test_srt_parse_and_write(tmp_path):
    path = tmp_path / "movie.srt"
    path.write_text(
        "1\n00:00:01,000 --> 00:00:03,000\n第一行\n第二行\n\n"
        "2\n00:00:04,000 --> 00:00:05,500\n你好\n\n",
        encoding="utf-8",
    )
    segments = list(formats.iter_srt_segments(path))
    assert len(segments) == 2
    assert segments[0].index == "1"
    assert segments[0].timing == "00:00:01,000 --> 00:00:03,000"
    assert segments[0].text == "第一行\n第二行"
    assert segments[1].text == "你好"

    out = StringIO()
    for seg in segments:
        formats.write_segment(out, "srt", seg, "перевод")
    assert out.getvalue() == (
        "1\n00:00:01,000 --> 00:00:03,000\nперевод\n\n"
        "2\n00:00:04,000 --> 00:00:05,500\nперевод\n\n"
    )


def test_srt_crlf_and_bom(tmp_path):
    path = tmp_path / "win.srt"
    path.write_bytes("\ufeff1\r\n00:00:01,000 --> 00:00:02,000\r\nпривет\r\n\r\n".encode("utf-8"))
    segments = list(formats.iter_srt_segments(path))
    assert len(segments) == 1
    assert segments[0].timing == "00:00:01,000 --> 00:00:02,000"
    assert segments[0].text == "привет"


def test_txt_blank_lines_preserved(tmp_path):
    path = tmp_path / "a.txt"
    path.write_text("aaa.\n\nbbb.\n", encoding="utf-8")
    segments = list(formats.iter_txt_segments(path, min_chars=1, max_chars=1000))
    assert [s.passthrough for s in segments] == [False, True, False]
    assert segments[0].text == "aaa."
    assert segments[2].text == "bbb."

    out = StringIO()
    for seg in segments:
        text = "" if seg.passthrough else seg.text
        formats.write_segment(out, "txt", seg, text)
    assert out.getvalue() == "aaa.\n\nbbb.\n"


def test_txt_long_line_splits_with_glue(tmp_path):
    line = "这是一个很长的句子。" * 30  # 300 символов без пробелов
    path = tmp_path / "long.txt"
    path.write_text(line + "\n", encoding="utf-8")

    segments = list(formats.iter_txt_segments(path, min_chars=10, max_chars=120))
    assert len(segments) > 1
    assert segments[-1].glue is False
    assert all(s.glue for s in segments[:-1])
    assert "".join(s.text for s in segments) == line
    assert all(len(s.text) <= 120 for s in segments)

    out = StringIO()
    for seg in segments:
        formats.write_segment(out, "txt", seg, seg.text)
    assert out.getvalue() == line + "\n"


def test_default_output_path():
    from pathlib import Path

    assert formats.default_output_path(Path("/tmp/movie.srt"), "ru") == Path("/tmp/movie.ru.srt")
    assert formats.default_output_path(Path("book.txt"), "ru") == Path("book.ru.txt")
