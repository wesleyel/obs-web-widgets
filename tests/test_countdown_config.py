from __future__ import annotations

import datetime as dt
import json

from obs_web_widgets.config import AppConfig, config_from_mapping, load_config, save_config, update_config
from obs_web_widgets.countdown import countdown_snapshot, parse_countdown_target


def test_parse_countdown_target_accepts_date_and_datetime() -> None:
    assert parse_countdown_target("2026-12-20") == dt.datetime(2026, 12, 20)
    assert parse_countdown_target("2026-12-20 12:30:00") == dt.datetime(2026, 12, 20, 12, 30)


def test_countdown_snapshot_calculates_remaining_time() -> None:
    config = AppConfig(countdown_name="Exam", countdown_target="2026-12-20 00:00:00")
    payload = countdown_snapshot(config, now=dt.datetime(2026, 12, 18, 23, 58, 30))

    assert payload["ok"] is True
    assert payload["name"] == "Exam"
    assert payload["remainingSeconds"] == 86490
    assert payload["days"] == 1
    assert payload["minutes"] == 1
    assert payload["seconds"] == 30


def test_config_read_write_and_validation(tmp_path) -> None:
    path = tmp_path / "config.json"
    config = config_from_mapping(
        {
            "lyricOffset": "0.25",
            "nowPlayingBundleID": "com.example.music",
            "pollInterval": "0.05",
            "countdownName": "考研",
            "countdownTarget": "2026-12-20",
        }
    )
    save_config(config, path)

    loaded = load_config(path)

    assert loaded.lyric_offset == 0.25
    assert loaded.now_playing_bundle_id == "com.example.music"
    assert loaded.poll_interval == 0.1
    assert loaded.countdown_name == "考研"
    assert json.loads(path.read_text("utf-8"))["countdownTarget"] == "2026-12-20"


def test_update_config_preserves_unspecified_values() -> None:
    current = AppConfig(lyric_offset=0.1, countdown_name="A")
    updated = update_config(current, {"countdownName": "B"})

    assert updated.lyric_offset == 0.1
    assert updated.countdown_name == "B"
