from obs_web_widgets.lyrics import (
    LyricBundle,
    LyricLine,
    amll_core_lines,
    parse_lrc,
    parse_yrc_core_lines,
)


def test_parse_lrc_supports_multiple_timestamps() -> None:
    lines = parse_lrc("[00:01.20][00:03.400]Hello\n[00:02]World")

    assert lines == [
        LyricLine(1.2, "Hello"),
        LyricLine(2.0, "World"),
        LyricLine(3.4, "Hello"),
    ]


def test_parse_yrc_core_lines_with_translation_and_romanization() -> None:
    raw = "[1000,2000](1000,500,0)你(1500,500,0)好"
    translations = {1000: "Hello"}
    romanizations = {1000: "ni hao"}

    lines = parse_yrc_core_lines(raw, translations, romanizations)

    assert lines == [
        {
            "startTime": 1000,
            "endTime": 3000,
            "words": [
                {"word": "你", "startTime": 1000, "endTime": 1500},
                {"word": "好", "startTime": 1500, "endTime": 2000},
            ],
            "translatedLyric": "Hello",
            "romanLyric": "ni hao",
            "isBG": False,
            "isDuet": False,
        }
    ]


def test_amll_core_lines_falls_back_to_lrc() -> None:
    bundle = LyricBundle([LyricLine(1.0, "First"), LyricLine(3.0, "Second")], 42, "ok")
    lines = amll_core_lines({"duration": 5}, bundle)

    assert lines[0]["startTime"] == 1000
    assert lines[0]["endTime"] == 3000
    assert lines[0]["words"][0]["word"] == "First"
