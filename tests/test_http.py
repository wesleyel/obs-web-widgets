from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request

import pytest

from obs_web_widgets.lyrics import LyricBundle, LyricLine
from obs_web_widgets.web import WidgetRuntime, create_http_server


@pytest.fixture
def test_server(tmp_path):
    runtime = WidgetRuntime(
        host="127.0.0.1",
        port=0,
        config_path=tmp_path / "config.json",
        command_path="/opt/homebrew/bin/obs-web-widgets",
        now_playing_reader=lambda: {},
    )
    with runtime.state_lock:
        runtime.now_playing = {
            "title": "Song",
            "artist": "Artist",
            "album": "Album",
            "duration": 12,
            "elapsed": 2,
            "playbackRate": 1,
            "timestamp": 0,
            "appBundleID": "com.netease.163music",
        }
        runtime.lyric_bundle = LyricBundle([LyricLine(0, "First"), LyricLine(5, "Second")], 123, "ok")

    server = create_http_server(runtime)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}", runtime
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def get_text(base_url: str, path: str) -> str:
    with urllib.request.urlopen(f"{base_url}{path}", timeout=3) as response:
        return response.read().decode("utf-8")


def get_json(base_url: str, path: str) -> dict:
    return json.loads(get_text(base_url, path))


def post_json(base_url: str, path: str, payload: dict) -> dict:
    request = urllib.request.Request(
        f"{base_url}{path}",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=3) as response:
        return json.loads(response.read().decode("utf-8"))


def test_http_routes_and_api_smoke(test_server) -> None:
    base_url, runtime = test_server

    assert "obs-web-widgets" in get_text(base_url, "/config")
    assert "obs-web-widgets" in get_text(base_url, "/")
    assert "EventSource" in get_text(base_url, "/lyrics")
    assert "LyricPlayer" in get_text(base_url, "/amll")
    assert "倒数日" in get_text(base_url, "/countdown")
    assert get_json(base_url, "/api/state")["track"]["title"] == "Song"
    assert get_json(base_url, "/api/countdown")["ok"] is True
    assert get_json(base_url, "/api/amll/lines")["lines"][0]["words"][0]["word"] == "First"
    assert get_json(base_url, "/api/config")["server"]["port"] == runtime.port
    assert get_json(base_url, "/api/autostart")["commandPath"] == "/opt/homebrew/bin/obs-web-widgets"

    updated = post_json(base_url, "/api/config", {"countdownName": "Exam", "lyricOffset": 0.2})
    assert updated["config"]["countdownName"] == "Exam"
    assert runtime.current_config().lyric_offset == 0.2


def test_events_stream_emits_state(test_server) -> None:
    base_url, _ = test_server

    with urllib.request.urlopen(f"{base_url}/events", timeout=3) as response:
        line = response.readline().decode("utf-8")

    assert line.startswith("data: ")
    assert json.loads(line.removeprefix("data: "))["track"]["title"] == "Song"


@pytest.mark.parametrize("path", ["/state", "/overlay", "/amll-player", "/amll/manifest.json", "/amll/lines.json"])
def test_removed_legacy_routes_return_404(test_server, path: str) -> None:
    base_url, _ = test_server

    with pytest.raises(urllib.error.HTTPError) as error:
        get_text(base_url, path)

    assert error.value.code == 404
