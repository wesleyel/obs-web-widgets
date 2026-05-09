from __future__ import annotations

import sys

import pytest

from obs_web_widgets.now_playing import now_playing_adapter_archive_path, read_now_playing


def test_now_playing_adapter_archive_is_packaged() -> None:
    assert now_playing_adapter_archive_path().endswith("mediaremote-adapter.tar.gz")


def test_read_now_playing_normalizes_mediaremote_adapter_payload(tmp_path) -> None:
    script = tmp_path / "fake_now_playing.py"
    script.write_text(
        "import json\n"
        "print(json.dumps({"
        "'title': 'Song',"
        "'artist': 'Artist',"
        "'album': 'Album',"
        "'duration': 12.5,"
        "'elapsedTime': 3,"
        "'playbackRate': 1,"
        "'timestamp': '2026-05-09T02:57:47Z',"
        "'bundleIdentifier': 'com.example.music'"
        "}))\n",
        "utf-8",
    )

    payload = read_now_playing(f"{sys.executable} {script}")

    assert payload["ok"] is True
    assert payload["title"] == "Song"
    assert payload["artist"] == "Artist"
    assert payload["album"] == "Album"
    assert payload["duration"] == 12.5
    assert payload["elapsed"] == 3
    assert payload["playbackRate"] == 1
    assert payload["appBundleID"] == "com.example.music"
    assert payload["timestamp"] == pytest.approx(1778295467)


def test_read_now_playing_raises_adapter_errors(tmp_path) -> None:
    script = tmp_path / "fake_error.py"
    script.write_text("print('{\"ok\": false, \"error\": \"boom\"}')\n", "utf-8")

    with pytest.raises(RuntimeError, match="boom"):
        read_now_playing(f"{sys.executable} {script}")
