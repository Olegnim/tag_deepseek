"""Тесты глоссария."""

from translator import glossary


def test_load_full_and_short_forms(tmp_path):
    path = tmp_path / "terms.tsv"
    path.write_text(
        "# комментарий\n"
        "zh\t人工智能\tискусственный интеллект\tтермин\n"
        "서버\tсервер\n"
        "en\tAI\tИИ\tаббревиатура\n",
        encoding="utf-8",
    )
    terms = glossary.load_glossary(path)
    assert len(terms) == 3
    assert terms[0].lang == "zh"
    assert terms[0].source == "人工智能"
    assert terms[0].note == "термин"
    assert terms[1].lang == ""
    assert terms[2].source == "AI"


def test_load_missing_file(tmp_path):
    assert glossary.load_glossary(tmp_path / "nope.tsv") == []


def test_select_terms_filters_by_lang_and_text():
    terms = [
        glossary.Term("zh", "人工智能", "искусственный интеллект"),
        glossary.Term("ko", "인공지능", "искусственный интеллект"),
        glossary.Term("", "AI", "ИИ"),
    ]
    selected = glossary.select_terms(terms, "zh", "Тут есть 人工智能 и AI")
    sources = {t.source for t in selected}
    assert sources == {"人工智能", "AI"}


def test_format_terms():
    terms = [glossary.Term("zh", "服务器", "сервер", "в IT")]
    text = glossary.format_terms(terms)
    assert "服务器 → сервер (в IT)" in text
    assert glossary.format_terms([]) == ""
