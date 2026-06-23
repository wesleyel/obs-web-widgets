from __future__ import annotations

import json
import threading
import time
import urllib.parse
from collections.abc import Callable
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from pathlib import Path
from typing import Any

from .autostart import autostart_status, install_autostart, uninstall_autostart
from .config import AppConfig, config_to_json, load_config, save_config, update_config
from .countdown import countdown_snapshot
from .lyrics import (
    LyricBundle,
    amll_lines_payload,
    empty_bundle,
    load_track_lyrics,
    lyric_snapshot,
    track_key,
)
from .now_playing import read_now_playing

NowPlayingReader = Callable[[], dict[str, Any]]


class WidgetRuntime:
    def __init__(
        self,
        *,
        host: str,
        port: int,
        config_path: Path,
        command_path: str,
        now_playing_reader: NowPlayingReader = read_now_playing,
    ) -> None:
        self.host = host
        self.port = port
        self.config_path = config_path
        self.command_path = command_path
        self.now_playing_reader = now_playing_reader

        self.state_lock = threading.RLock()
        self.config = load_config(config_path)
        self.now_playing: dict[str, Any] = {}
        self.lyric_bundle = empty_bundle()
        self.lyrics_track_key = ""
        self.last_error = ""
        self.lyrics_cache: dict[str, LyricBundle] = {}
        self._polling_started = False

    def set_bound_port(self, port: int) -> None:
        self.port = port

    def current_config(self) -> AppConfig:
        with self.state_lock:
            return self.config

    def update_config(self, payload: dict[str, Any]) -> AppConfig:
        with self.state_lock:
            self.config = update_config(self.config, payload)
            self.lyrics_track_key = ""
            config = self.config
        save_config(config, self.config_path)
        return config

    def snapshot(self) -> dict[str, Any]:
        with self.state_lock:
            track = dict(self.now_playing)
            bundle = self.lyric_bundle
            error = self.last_error
            config = self.config
        return lyric_snapshot(track, bundle, lyric_offset=config.lyric_offset, last_error=error)

    def countdown_snapshot(self) -> dict[str, Any]:
        return countdown_snapshot(self.current_config())

    def amll_lines_payload(self) -> dict[str, Any]:
        with self.state_lock:
            track = dict(self.now_playing)
            bundle = self.lyric_bundle
        return amll_lines_payload(track, bundle)

    def config_payload(self) -> dict[str, Any]:
        return config_to_json(self.current_config(), path=self.config_path, host=self.host, port=self.port)

    def autostart_payload(self) -> dict[str, Any]:
        return {
            **autostart_status(),
            "commandPath": self.command_path,
        }

    def set_autostart(self, enabled: bool) -> dict[str, Any]:
        if enabled:
            install_autostart(self.command_path, host=self.host, port=self.port, config_path=self.config_path)
        else:
            uninstall_autostart()
        return self.autostart_payload()

    def start_polling(self) -> None:
        if self._polling_started:
            return
        self._polling_started = True
        thread = threading.Thread(target=self.poll_now_playing, name="obs-web-widgets-poller", daemon=True)
        thread.start()

    def poll_now_playing(self) -> None:
        while True:
            self.poll_once()
            time.sleep(self.current_config().poll_interval)

    def poll_once(self) -> None:
        try:
            track = self.now_playing_reader()
            config = self.current_config()
            app_bundle = str(track.get("appBundleID") or "")
            if config.now_playing_bundle_id and app_bundle and app_bundle != config.now_playing_bundle_id:
                with self.state_lock:
                    self.now_playing = {}
                    self.lyric_bundle = empty_bundle("")
                    self.last_error = ""
                    self.lyrics_track_key = ""
                return

            key = track_key(track)
            with self.state_lock:
                self.now_playing = track
                changed = key != self.lyrics_track_key

            if changed:
                bundle = load_track_lyrics(track, self.lyrics_cache)
                with self.state_lock:
                    self.lyric_bundle = bundle
                    self.lyrics_track_key = key
                    self.last_error = ""
            else:
                with self.state_lock:
                    self.last_error = ""
        except Exception as error:
            with self.state_lock:
                self.last_error = str(error)
                if not self.now_playing:
                    self.lyric_bundle = empty_bundle("读取当前播放失败")


def render_template(template_name: str) -> str:
    return files("obs_web_widgets").joinpath("templates", template_name).read_text("utf-8")


class WidgetHTTPServer(ThreadingHTTPServer):
    runtime: WidgetRuntime


def create_http_server(runtime: WidgetRuntime) -> WidgetHTTPServer:
    server = WidgetHTTPServer((runtime.host, runtime.port), WidgetRequestHandler)
    runtime.set_bound_port(server.server_address[1])
    server.runtime = runtime
    return server


class WidgetRequestHandler(BaseHTTPRequestHandler):
    server: WidgetHTTPServer
    server_version = "obs-web-widgets/0.1"

    def log_message(self, format: str, *args: Any) -> None:
        return

    @property
    def runtime(self) -> WidgetRuntime:
        return self.server.runtime

    def send_bytes(self, body: bytes, content_type: str, status: HTTPStatus = HTTPStatus.OK) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_bytes(body, "application/json; charset=utf-8", status)

    def send_text(self, body: str, content_type: str, status: HTTPStatus = HTTPStatus.OK) -> None:
        self.send_bytes(body.encode("utf-8"), f"{content_type}; charset=utf-8", status)

    def send_template(self, template_name: str) -> None:
        self.send_text(render_template(template_name), "text/html")

    def read_json_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0") or 0)
        raw = self.rfile.read(length).decode("utf-8") if length else ""
        if not raw:
            return {}
        content_type = self.headers.get("Content-Type", "")
        if "application/json" in content_type:
            payload = json.loads(raw)
        else:
            payload = {key: values[-1] for key, values in urllib.parse.parse_qs(raw).items()}
        if not isinstance(payload, dict):
            raise ValueError("JSON body must be an object")
        return payload

    def do_OPTIONS(self) -> None:
        self.send_response(HTTPStatus.NO_CONTENT)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self) -> None:
        path = urllib.parse.urlparse(self.path).path
        if path in {"/", "/config"}:
            self.send_template("config.html")
            return
        if path == "/lyrics":
            self.send_template("lyrics.html")
            return
        if path == "/amll":
            self.send_template("amll.html")
            return
        if path == "/countdown":
            self.send_template("countdown.html")
            return
        if path == "/api/state":
            self.send_json(self.runtime.snapshot())
            return
        if path == "/api/countdown":
            self.send_json(self.runtime.countdown_snapshot())
            return
        if path == "/api/amll/lines":
            self.send_json(self.runtime.amll_lines_payload())
            return
        if path == "/api/config":
            self.send_json(self.runtime.config_payload())
            return
        if path == "/api/autostart":
            self.send_json(self.runtime.autostart_payload())
            return
        if path == "/events":
            self.send_events()
            return
        if path == "/favicon.ico":
            self.send_bytes(b"", "image/x-icon", HTTPStatus.NO_CONTENT)
            return
        self.send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        path = urllib.parse.urlparse(self.path).path
        try:
            payload = self.read_json_body()
        except (json.JSONDecodeError, ValueError) as error:
            self.send_json({"ok": False, "error": str(error)}, HTTPStatus.BAD_REQUEST)
            return

        if path == "/api/config":
            config = self.runtime.update_config(payload)
            self.send_json(
                {
                    "ok": True,
                    "config": config.to_public_dict(),
                    "configPath": str(self.runtime.config_path),
                }
            )
            return
        if path == "/api/autostart":
            enabled = bool(payload.get("enabled"))
            self.send_json(self.runtime.set_autostart(enabled))
            return
        self.send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)

    def send_events(self) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        try:
            while True:
                body = json.dumps(self.runtime.snapshot(), ensure_ascii=False)
                self.wfile.write(f"data: {body}\n\n".encode())
                self.wfile.flush()
                time.sleep(0.25)
        except (BrokenPipeError, ConnectionResetError):
            return
