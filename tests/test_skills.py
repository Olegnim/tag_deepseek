"""Тесты набора навыков перевода."""

from pathlib import Path

from translator.skills import load_skills, skill_names

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SKILLS_DIR = PROJECT_ROOT / "skills"


def test_skill_names_include_language_and_subtitles():
    assert skill_names("zh", "srt") == ["base.md", "zh-ru.md", "subtitles.md"]
    assert skill_names("ja", "txt") == ["base.md", "ja-ru.md"]
    assert skill_names("auto", "txt") == ["base.md"]


def test_all_skill_files_exist_and_load():
    for lang in ("zh", "ko", "en", "ja"):
        names = skill_names(lang, "srt")
        for name in names:
            assert (SKILLS_DIR / name).is_file(), f"нет файла навыка {name}"
        text = load_skills(SKILLS_DIR, names)
        assert len(text) > 100


def test_load_skills_skips_missing(tmp_path):
    assert load_skills(tmp_path, ["nope.md"]) == ""
