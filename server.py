#!/usr/bin/env python3
from __future__ import annotations

import bisect
import datetime as dt
import html
import json
import os
import re
import shlex
import shutil
import subprocess
import threading
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader

try:
    from config import COUNTDOWN_NAME, COUNTDOWN_TARGET
except ImportError:
    COUNTDOWN_NAME = ""
    COUNTDOWN_TARGET = ""


ROOT = Path(__file__).resolve().parent
TEMPLATES = Environment(loader=FileSystemLoader(ROOT / "templates"))
NOW_PLAYING_SWIFT = ROOT / "now_playing.swift"
NOW_PLAYING_COMMAND = os.environ.get("NOW_PLAYING_COMMAND", "")

HOST = os.environ.get("OBS_LYRICS_HOST", "127.0.0.1")
PORT = int(os.environ.get("OBS_LYRICS_PORT", "17363"))
SOURCE_BUNDLE = os.environ.get("OBS_LYRICS_SOURCE_BUNDLE", "com.netease.163music")
POLL_INTERVAL = float(os.environ.get("OBS_LYRICS_POLL_INTERVAL", "0.6"))
LYRIC_OFFSET = float(os.environ.get("OBS_LYRICS_OFFSET", "0"))

NETEASE_SEARCH = "https://music.163.com/api/search/get/web"
NETEASE_LYRIC = "https://music.163.com/api/song/lyric"
NETEASE_LYRIC_V1 = "https://interface.music.163.com/api/song/lyric/v1"
HTTP_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X) OBS lyrics overlay",
    "Referer": "https://music.163.com/",
}


@dataclass(frozen=True)
class LyricLine:
    at: float
    text: str


@dataclass(frozen=True)
class LyricBundle:
    lines: list[LyricLine]
    song_id: int | None
    message: str
    raw_lrc: str = ""
    raw_yrc: str = ""
    raw_translation_lrc: str = ""
    raw_roman_lrc: str = ""
    source_format: str = "none"


state_lock = threading.RLock()
now_playing: dict[str, Any] = {}
lyric_bundle = LyricBundle([], None, "等待网易云音乐播放")
lyrics_track_key = ""
last_error = ""
lyrics_cache: dict[str, LyricBundle] = {}


def read_now_playing() -> dict[str, Any]:
    if NOW_PLAYING_COMMAND:
        command = shlex.split(NOW_PLAYING_COMMAND)
    else:
        swift = shutil.which("swift")
        if swift is None:
            raise RuntimeError("找不到 swift，无法读取 macOS Now Playing")
        command = [swift, str(NOW_PLAYING_SWIFT)]

    raw = subprocess.check_output(command, text=True, timeout=3, cwd=ROOT)
    payload = json.loads(raw)
    if not payload.get("ok"):
        raise RuntimeError(payload.get("error", "Now Playing 读取失败"))
    return payload


def http_json(url: str, params: dict[str, str]) -> dict[str, Any]:
    query = urllib.parse.urlencode(params)
    request = urllib.request.Request(f"{url}?{query}", headers=HTTP_HEADERS)
    with urllib.request.urlopen(request, timeout=8) as response:
        return json.loads(response.read().decode("utf-8"))


def normalize(value: str) -> str:
    value = value.lower()
    value = re.sub(r"[\s\-_/|:：·・,，.。'\"“”‘’()（）\[\]【】]+", "", value)
    return value


def similarity(left: str, right: str) -> float:
    left = normalize(left)
    right = normalize(right)
    if not left or not right:
        return 0
    if left == right:
        return 1
    if left in right or right in left:
        return 0.86

    # Lightweight ratio without pulling in external dependencies.
    common = sum(1 for char in left if char in right)
    return common / max(len(left), len(right))


def candidate_artists(song: dict[str, Any]) -> str:
    artists = song.get("artists") or song.get("ar") or []
    names = [artist.get("name", "") for artist in artists if isinstance(artist, dict)]
    return " / ".join(name for name in names if name)


def score_song(song: dict[str, Any], title: str, artist: str, duration: float) -> float:
    name = str(song.get("name", ""))
    artists = candidate_artists(song)
    score = similarity(name, title) * 0.72 + similarity(artists, artist) * 0.22

    song_duration_ms = song.get("duration") or song.get("dt") or 0
    if duration and song_duration_ms:
        delta = abs(float(song_duration_ms) / 1000 - duration)
        duration_score = max(0.0, 1.0 - delta / 30)
        score += duration_score * 0.06

    return score


def search_song_id(title: str, artist: str, duration: float) -> int | None:
    payload = http_json(
        NETEASE_SEARCH,
        {
            "s": f"{title} {artist}".strip(),
            "type": "1",
            "offset": "0",
            "total": "true",
            "limit": "10",
        },
    )
    songs = payload.get("result", {}).get("songs") or []
    if not songs:
        return None

    best = max(songs, key=lambda item: score_song(item, title, artist, duration))
    if score_song(best, title, artist, duration) < 0.45:
        return None
    song_id = best.get("id")
    return int(song_id) if song_id else None


def parse_lrc(raw: str) -> list[LyricLine]:
    parsed: list[LyricLine] = []
    pattern = re.compile(r"\[(\d{1,2}):(\d{2})(?:[.:](\d{1,3}))?]")

    for raw_line in raw.splitlines():
        matches = list(pattern.finditer(raw_line))
        if not matches:
            continue

        text = pattern.sub("", raw_line).strip()
        if not text:
            continue

        for match in matches:
            minute = int(match.group(1))
            second = int(match.group(2))
            fraction = match.group(3) or "0"
            millisecond = int(fraction.ljust(3, "0")[:3])
            parsed.append(LyricLine(minute * 60 + second + millisecond / 1000, text))

    parsed.sort(key=lambda line: line.at)
    return parsed


def lyric_field(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if isinstance(value, dict):
        return str(value.get("lyric") or "")
    return ""


def looks_like_netease_yrc(raw: str) -> bool:
    return bool(re.search(r"(?m)^\[\d+,\d+]\(\d+,\d+,0\)", raw))


def clean_netease_yrc(raw: str) -> str:
    lines = [line for line in raw.splitlines() if re.match(r"^\[\d+,\d+]", line)]
    return "\n".join(lines)


def fetch_lyrics(song_id: int) -> LyricBundle:
    legacy_payload = http_json(
        NETEASE_LYRIC,
        {
            "id": str(song_id),
            "lv": "-1",
            "kv": "1",
            "tv": "-1",
        },
    )
    try:
        v1_payload = http_json(
            NETEASE_LYRIC_V1,
            {
                "id": str(song_id),
                "cp": "false",
                "lv": "-1",
                "tv": "-1",
                "rv": "-1",
                "kv": "-1",
                "yv": "-1",
                "ytv": "-1",
                "yrv": "-1",
            },
        )
    except Exception:
        v1_payload = {}
    raw_lrc = lyric_field(legacy_payload, "lrc")
    raw_yrc_candidate = lyric_field(v1_payload, "yrc")
    raw_yrc = clean_netease_yrc(raw_yrc_candidate) if looks_like_netease_yrc(raw_yrc_candidate) else ""
    raw_translation_lrc = lyric_field(legacy_payload, "tlyric") or lyric_field(v1_payload, "tlyric")
    raw_roman_lrc = lyric_field(v1_payload, "romalrc")
    parsed = parse_lrc(raw_lrc)

    if parsed:
        return LyricBundle(
            parsed,
            song_id,
            "歌词已加载",
            raw_lrc=raw_lrc,
            raw_yrc=raw_yrc,
            raw_translation_lrc=raw_translation_lrc,
            raw_roman_lrc=raw_roman_lrc,
            source_format="yrc" if raw_yrc else "lrc",
        )
    if raw_lrc.strip():
        return LyricBundle(
            [LyricLine(0, raw_lrc.strip())],
            song_id,
            "歌词已加载",
            raw_lrc=raw_lrc,
            raw_yrc=raw_yrc,
            raw_translation_lrc=raw_translation_lrc,
            raw_roman_lrc=raw_roman_lrc,
            source_format="plain",
        )
    return LyricBundle(
        [],
        song_id,
        "未找到可用歌词",
        raw_lrc=raw_lrc,
        raw_yrc=raw_yrc,
        raw_translation_lrc=raw_translation_lrc,
        raw_roman_lrc=raw_roman_lrc,
        source_format="none",
    )


def load_track_lyrics(track: dict[str, Any]) -> LyricBundle:
    title = str(track.get("title") or "").strip()
    artist = str(track.get("artist") or "").strip()
    duration = float(track.get("duration") or 0)
    key = track_key(track)

    if key in lyrics_cache:
        return lyrics_cache[key]

    if not title:
        result = LyricBundle([], None, "等待网易云音乐播放")
        lyrics_cache[key] = result
        return result

    song_id = search_song_id(title, artist, duration)
    if song_id is None:
        result = LyricBundle([], None, "网易云未匹配到歌曲")
        lyrics_cache[key] = result
        return result

    result = fetch_lyrics(song_id)
    lyrics_cache[key] = result
    return result


def track_key(track: dict[str, Any]) -> str:
    return "|".join(
        [
            normalize(str(track.get("title") or "")),
            normalize(str(track.get("artist") or "")),
            str(round(float(track.get("duration") or 0))),
        ]
    )


def current_elapsed(track: dict[str, Any]) -> float:
    elapsed = float(track.get("elapsed") or 0)
    timestamp = float(track.get("timestamp") or track.get("capturedAt") or time.time())
    playback_rate = float(track.get("playbackRate") or 0)
    if playback_rate > 0:
        elapsed += (time.time() - timestamp) * playback_rate
    return max(0.0, elapsed + LYRIC_OFFSET)


def snapshot() -> dict[str, Any]:
    with state_lock:
        track = dict(now_playing)
        bundle = lyric_bundle
        lines = list(bundle.lines)
        song_id = bundle.song_id
        message = bundle.message
        error = last_error

    elapsed = current_elapsed(track) if track else 0
    times = [line.at for line in lines]
    index = bisect.bisect_right(times, elapsed) - 1
    current = lines[index].text if 0 <= index < len(lines) else message
    previous = lines[index - 1].text if index > 0 else ""
    next_line = lines[index + 1].text if 0 <= index + 1 < len(lines) else ""

    return {
        "ok": bool(track),
        "track": {
            "title": track.get("title", ""),
            "artist": track.get("artist", ""),
            "album": track.get("album", ""),
            "duration": track.get("duration", 0),
            "elapsed": elapsed,
            "playbackRate": track.get("playbackRate", 0),
            "appBundleID": track.get("appBundleID", ""),
        },
        "neteaseSongID": song_id,
        "amll": {
            "sourceFormat": bundle.source_format,
            "hasLrc": bool(bundle.raw_lrc.strip()),
            "hasYrc": bool(bundle.raw_yrc.strip()),
            "hasTranslation": bool(bundle.raw_translation_lrc.strip()),
            "hasRomanization": bool(bundle.raw_roman_lrc.strip()),
            "manifest": "/amll/manifest.json",
            "coreLines": "/amll/lines.json",
            "lrc": "/amll/lyrics.lrc",
            "ttml": "/amll/lyrics.ttml",
            "yrc": "/amll/lyrics.yrc" if bundle.raw_yrc.strip() else None,
        },
        "lyric": {
            "previous": previous,
            "current": current,
            "next": next_line,
            "lineCount": len(lines),
        },
        "message": message,
        "error": error,
        "updatedAt": time.time(),
    }


def parse_countdown_target(value: Any) -> dt.datetime | None:
    if isinstance(value, dt.datetime):
        if value.tzinfo is not None:
            return value.astimezone().replace(tzinfo=None)
        return value
    if isinstance(value, dt.date):
        return dt.datetime.combine(value, dt.time.min)
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None
        try:
            parsed = dt.datetime.fromisoformat(raw)
        except ValueError:
            try:
                parsed = dt.datetime.combine(dt.date.fromisoformat(raw), dt.time.min)
            except ValueError:
                return None
        if parsed.tzinfo is not None:
            return parsed.astimezone().replace(tzinfo=None)
        return parsed
    return None


def countdown_snapshot() -> dict[str, Any]:
    target = parse_countdown_target(COUNTDOWN_TARGET)
    if target is None:
        return {
            "ok": False,
            "name": str(COUNTDOWN_NAME or ""),
            "target": str(COUNTDOWN_TARGET or ""),
            "message": "倒数日配置无效，请检查 config.py 中的 COUNTDOWN_TARGET",
            "updatedAt": time.time(),
        }

    now = dt.datetime.now()
    remaining_seconds = max(0, int((target - now).total_seconds()))
    days, remainder = divmod(remaining_seconds, 24 * 60 * 60)
    hours, remainder = divmod(remainder, 60 * 60)
    minutes, seconds = divmod(remainder, 60)

    return {
        "ok": True,
        "name": str(COUNTDOWN_NAME or ""),
        "target": target.isoformat(sep=" ", timespec="seconds"),
        "expired": remaining_seconds == 0,
        "remainingSeconds": remaining_seconds,
        "days": days,
        "hours": hours,
        "minutes": minutes,
        "seconds": seconds,
        "updatedAt": time.time(),
    }


def current_track_and_bundle() -> tuple[dict[str, Any], LyricBundle]:
    with state_lock:
        return dict(now_playing), lyric_bundle


def format_lrc_time(seconds: float) -> str:
    seconds = max(0.0, seconds)
    minute = int(seconds // 60)
    second = seconds - minute * 60
    return f"{minute:02d}:{second:06.3f}"


def format_ttml_time(seconds: float) -> str:
    return f"{max(0.0, seconds):.3f}"


def format_duration_tag(seconds: float) -> str:
    seconds = max(0.0, seconds)
    minute = int(seconds // 60)
    second = seconds - minute * 60
    return f"{minute}:{second:06.3f}"


def generated_lrc_from_lines(lines: list[LyricLine]) -> str:
    return "\n".join(f"[{format_lrc_time(line.at)}]{line.text}" for line in lines)


def lrc_with_metadata(track: dict[str, Any], bundle: LyricBundle) -> str:
    raw_lrc = bundle.raw_lrc.strip() or generated_lrc_from_lines(bundle.lines)
    if not raw_lrc:
        return ""

    metadata = {
        "ti": str(track.get("title") or ""),
        "ar": str(track.get("artist") or ""),
        "al": str(track.get("album") or ""),
        "length": format_duration_tag(float(track.get("duration") or 0)),
    }
    existing_tags = set(re.findall(r"^\[([A-Za-z]+):", raw_lrc, flags=re.MULTILINE))
    header = [f"[{key}:{value}]" for key, value in metadata.items() if value and key not in existing_tags]
    if bundle.song_id is not None:
        header.append(f"[netease:{bundle.song_id}]")

    return "\n".join([*header, "", raw_lrc]).strip() + "\n"


def timed_text_by_millisecond(raw_lrc: str) -> dict[int, str]:
    result: dict[int, str] = {}
    for line in parse_lrc(raw_lrc):
        text = line.text.strip()
        if text:
            result[int(round(line.at * 1000))] = text
    return result


def nearest_timed_text(texts: dict[int, str], timestamp: int, tolerance: int = 750) -> str:
    if not texts:
        return ""
    nearest = min(texts, key=lambda value: abs(value - timestamp))
    if abs(nearest - timestamp) > tolerance:
        return ""
    return texts[nearest]


def line_end_at(index: int, lines: list[LyricLine], duration: float) -> float:
    start = lines[index].at
    if index + 1 < len(lines):
        end = lines[index + 1].at
    elif duration > start:
        end = duration
    else:
        end = start + 5
    return max(end, start + 0.2)


def parse_yrc_core_lines(raw_yrc: str, translations: dict[int, str], romanizations: dict[int, str]) -> list[dict[str, Any]]:
    core_lines: list[dict[str, Any]] = []
    line_pattern = re.compile(r"^\[(\d+),(\d+)](.*)$")
    word_pattern = re.compile(r"\((\d+),(\d+),\d+\)(.*?)(?=\(\d+,\d+,\d+\)|$)")

    for raw_line in raw_yrc.splitlines():
        line_match = line_pattern.match(raw_line)
        if not line_match:
            continue

        line_start = int(line_match.group(1))
        line_duration = int(line_match.group(2))
        body = line_match.group(3)
        words: list[dict[str, Any]] = []

        for word_match in word_pattern.finditer(body):
            word_start = int(word_match.group(1))
            word_duration = int(word_match.group(2))
            word = word_match.group(3)
            if not word:
                continue
            words.append(
                {
                    "word": word,
                    "startTime": word_start,
                    "endTime": max(word_start + word_duration, word_start + 1),
                }
            )

        if not words:
            continue

        line_end = max(line_start + line_duration, max(word["endTime"] for word in words))
        core_lines.append(
            {
                "startTime": line_start,
                "endTime": line_end,
                "words": words,
                "translatedLyric": nearest_timed_text(translations, line_start),
                "romanLyric": nearest_timed_text(romanizations, line_start),
                "isBG": False,
                "isDuet": False,
            }
        )

    return core_lines


def lrc_core_lines(track: dict[str, Any], bundle: LyricBundle) -> list[dict[str, Any]]:
    duration = float(track.get("duration") or 0)
    translations = timed_text_by_millisecond(bundle.raw_translation_lrc)
    romanizations = timed_text_by_millisecond(bundle.raw_roman_lrc)
    core_lines: list[dict[str, Any]] = []

    for index, line in enumerate(bundle.lines):
        start = int(round(line.at * 1000))
        end = int(round(line_end_at(index, bundle.lines, duration) * 1000))
        core_lines.append(
            {
                "startTime": start,
                "endTime": max(end, start + 1),
                "words": [
                    {
                        "word": line.text,
                        "startTime": start,
                        "endTime": max(end, start + 1),
                    }
                ],
                "translatedLyric": nearest_timed_text(translations, start),
                "romanLyric": nearest_timed_text(romanizations, start),
                "isBG": False,
                "isDuet": False,
            }
        )

    return core_lines


def amll_core_lines(track: dict[str, Any], bundle: LyricBundle) -> list[dict[str, Any]]:
    translations = timed_text_by_millisecond(bundle.raw_translation_lrc)
    romanizations = timed_text_by_millisecond(bundle.raw_roman_lrc)
    if bundle.raw_yrc.strip():
        yrc_lines = parse_yrc_core_lines(bundle.raw_yrc, translations, romanizations)
        if yrc_lines:
            return yrc_lines
    return lrc_core_lines(track, bundle)


def ttml_from_bundle(track: dict[str, Any], bundle: LyricBundle) -> str:
    title = html.escape(str(track.get("title") or ""))
    artist = html.escape(str(track.get("artist") or ""))
    album = html.escape(str(track.get("album") or ""))
    duration = float(track.get("duration") or 0)
    lines = bundle.lines
    translations = timed_text_by_millisecond(bundle.raw_translation_lrc)
    romanizations = timed_text_by_millisecond(bundle.raw_roman_lrc)

    body_lines: list[str] = []
    for index, line in enumerate(lines, start=1):
        begin = format_ttml_time(line.at)
        end = format_ttml_time(line_end_at(index - 1, lines, duration))
        key = f"L{index}"
        text = html.escape(line.text)
        body_lines.append(f'      <p begin="{begin}" end="{end}" itunes:key="{key}" ttm:agent="v1">')
        body_lines.append(f'        <span begin="{begin}" end="{end}">{text}</span>')

        timestamp = int(round(line.at * 1000))
        translation = translations.get(timestamp)
        if translation:
            body_lines.append(
                f'        <span ttm:role="x-translation" xml:lang="zh-Hans">{html.escape(translation)}</span>'
            )
        romanization = romanizations.get(timestamp)
        if romanization:
            body_lines.append(
                f'        <span ttm:role="x-roman" xml:lang="und-Latn">{html.escape(romanization)}</span>'
            )

        body_lines.append("      </p>")

    body = "\n".join(body_lines)
    source = html.escape(bundle.source_format)
    netease_id = html.escape(str(bundle.song_id or ""))

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<tt xmlns="http://www.w3.org/ns/ttml"
    xmlns:ttm="http://www.w3.org/ns/ttml#metadata"
    xmlns:tts="http://www.w3.org/ns/ttml#styling"
    xmlns:itunes="http://music.apple.com/lyric-ttml-internal"
    xmlns:amll="http://www.example.com/ns/amll"
    xml:lang="und"
    itunes:timing="Line">
  <head>
    <metadata>
      <ttm:title>{title}</ttm:title>
      <ttm:agent type="person" xml:id="v1">
        <ttm:name type="full">{artist}</ttm:name>
      </ttm:agent>
      <amll:meta key="musicName" value="{title}" />
      <amll:meta key="artists" value="{artist}" />
      <amll:meta key="album" value="{album}" />
      <amll:meta key="neteaseSongId" value="{netease_id}" />
      <amll:meta key="sourceFormat" value="{source}" />
    </metadata>
  </head>
  <body>
    <div itunes:song-part="Verse">
{body}
    </div>
  </body>
</tt>
"""


def amll_manifest(base_url: str) -> dict[str, Any]:
    track, bundle = current_track_and_bundle()
    formats = {
        "lrc": f"{base_url}/amll/lyrics.lrc" if bundle.raw_lrc or bundle.lines else None,
        "ttml": f"{base_url}/amll/lyrics.ttml" if bundle.lines else None,
        "yrc": f"{base_url}/amll/lyrics.yrc" if bundle.raw_yrc else None,
        "coreLines": f"{base_url}/amll/lines.json" if bundle.lines else None,
        "translationLrc": f"{base_url}/amll/translation.lrc" if bundle.raw_translation_lrc else None,
        "romanLrc": f"{base_url}/amll/roman.lrc" if bundle.raw_roman_lrc else None,
    }

    return {
        "ok": bool(track and bundle.lines),
        "track": {
            "title": track.get("title", ""),
            "artist": track.get("artist", ""),
            "album": track.get("album", ""),
            "duration": track.get("duration", 0),
            "appBundleID": track.get("appBundleID", ""),
        },
        "neteaseSongID": bundle.song_id,
        "sourceFormat": bundle.source_format,
        "formats": {key: value for key, value in formats.items() if value},
        "message": bundle.message,
        "updatedAt": time.time(),
    }


def poll_now_playing() -> None:
    global last_error, lyric_bundle, lyrics_track_key, now_playing

    while True:
        try:
            track = read_now_playing()
            app_bundle = str(track.get("appBundleID") or "")
            if SOURCE_BUNDLE and app_bundle and app_bundle != SOURCE_BUNDLE:
                with state_lock:
                    now_playing = track
                    lyric_bundle = LyricBundle([], None, f"当前 Now Playing 来源不是网易云: {app_bundle}")
                    last_error = ""
                    lyrics_track_key = ""
                time.sleep(POLL_INTERVAL)
                continue

            key = track_key(track)
            with state_lock:
                now_playing = track
                changed = key != lyrics_track_key

            if changed:
                bundle = load_track_lyrics(track)
                with state_lock:
                    lyric_bundle = bundle
                    lyrics_track_key = key
                    last_error = ""
            else:
                with state_lock:
                    last_error = ""
        except Exception as error:  # noqa: BLE001
            with state_lock:
                last_error = str(error)
                if not now_playing:
                    lyric_bundle = LyricBundle([], None, "读取当前播放失败")

        time.sleep(POLL_INTERVAL)


class Handler(BaseHTTPRequestHandler):
    server_version = "OBSLyrics/1.0"

    def log_message(self, format: str, *args: Any) -> None:
        return

    def send_bytes(self, body: bytes, content_type: str, status: HTTPStatus = HTTPStatus.OK) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_text(self, body: str, content_type: str, status: HTTPStatus = HTTPStatus.OK) -> None:
        self.send_bytes(body.encode("utf-8"), f"{content_type}; charset=utf-8", status)

    def send_template(self, template_name: str) -> None:
        body = TEMPLATES.get_template(template_name).render().encode("utf-8")
        self.send_bytes(body, "text/html; charset=utf-8")

    def request_base_url(self) -> str:
        host = self.headers.get("Host") or f"{HOST}:{PORT}"
        return f"http://{host}"

    def do_GET(self) -> None:  # noqa: N802
        path = urllib.parse.urlparse(self.path).path
        if path in {"/amll", "/amll-player"}:
            self.send_template("amll_player.html")
            return
        if path == "/countdown":
            self.send_template("countdown.html")
            return
        if path in {"/", "/overlay"}:
            self.send_template("overlay.html")
            return
        if path in {"/state", "/api/state"}:
            body = json.dumps(snapshot(), ensure_ascii=False).encode("utf-8")
            self.send_bytes(body, "application/json; charset=utf-8")
            return
        if path == "/api/countdown":
            body = json.dumps(countdown_snapshot(), ensure_ascii=False).encode("utf-8")
            self.send_bytes(body, "application/json; charset=utf-8")
            return
        if path == "/amll/manifest.json":
            body = json.dumps(amll_manifest(self.request_base_url()), ensure_ascii=False).encode("utf-8")
            self.send_bytes(body, "application/json; charset=utf-8")
            return
        if path == "/amll/lines.json":
            track, bundle = current_track_and_bundle()
            body = json.dumps(
                {
                    "ok": bool(bundle.lines),
                    "track": {
                        "title": track.get("title", ""),
                        "artist": track.get("artist", ""),
                        "album": track.get("album", ""),
                        "duration": track.get("duration", 0),
                        "appBundleID": track.get("appBundleID", ""),
                    },
                    "neteaseSongID": bundle.song_id,
                    "sourceFormat": bundle.source_format,
                    "lines": amll_core_lines(track, bundle),
                    "message": bundle.message,
                    "updatedAt": time.time(),
                },
                ensure_ascii=False,
            ).encode("utf-8")
            self.send_bytes(body, "application/json; charset=utf-8")
            return
        if path == "/amll/lyrics.lrc":
            track, bundle = current_track_and_bundle()
            lrc = lrc_with_metadata(track, bundle)
            if not lrc:
                self.send_text(json.dumps({"error": bundle.message}, ensure_ascii=False), "application/json", HTTPStatus.NOT_FOUND)
                return
            self.send_text(lrc, "text/plain")
            return
        if path == "/amll/lyrics.ttml":
            track, bundle = current_track_and_bundle()
            if not bundle.lines:
                self.send_text(json.dumps({"error": bundle.message}, ensure_ascii=False), "application/json", HTTPStatus.NOT_FOUND)
                return
            self.send_text(ttml_from_bundle(track, bundle), "application/ttml+xml")
            return
        if path == "/amll/lyrics.yrc":
            _, bundle = current_track_and_bundle()
            if not bundle.raw_yrc.strip():
                self.send_text(json.dumps({"error": "当前歌曲没有网易云 YRC 逐字歌词"}, ensure_ascii=False), "application/json", HTTPStatus.NOT_FOUND)
                return
            self.send_text(bundle.raw_yrc.strip() + "\n", "text/plain")
            return
        if path == "/amll/translation.lrc":
            _, bundle = current_track_and_bundle()
            if not bundle.raw_translation_lrc.strip():
                self.send_text(json.dumps({"error": "当前歌曲没有翻译 LRC"}, ensure_ascii=False), "application/json", HTTPStatus.NOT_FOUND)
                return
            self.send_text(bundle.raw_translation_lrc.strip() + "\n", "text/plain")
            return
        if path == "/amll/roman.lrc":
            _, bundle = current_track_and_bundle()
            if not bundle.raw_roman_lrc.strip():
                self.send_text(json.dumps({"error": "当前歌曲没有音译 LRC"}, ensure_ascii=False), "application/json", HTTPStatus.NOT_FOUND)
                return
            self.send_text(bundle.raw_roman_lrc.strip() + "\n", "text/plain")
            return
        if path == "/events":
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "keep-alive")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            try:
                while True:
                    body = json.dumps(snapshot(), ensure_ascii=False)
                    self.wfile.write(f"data: {body}\n\n".encode("utf-8"))
                    self.wfile.flush()
                    time.sleep(0.25)
            except (BrokenPipeError, ConnectionResetError):
                return
        if path == "/favicon.ico":
            self.send_bytes(b"", "image/x-icon", HTTPStatus.NO_CONTENT)
            return

        self.send_bytes(
            json.dumps({"error": "not found"}).encode("utf-8"),
            "application/json; charset=utf-8",
            HTTPStatus.NOT_FOUND,
        )


def main() -> None:
    thread = threading.Thread(target=poll_now_playing, daemon=True)
    thread.start()

    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"OBS Browser Source URL: http://{HOST}:{PORT}/")
    print(f"AMLL Player URL: http://{HOST}:{PORT}/amll")
    print(f"State endpoint: http://{HOST}:{PORT}/state")
    print(f"Countdown endpoint: http://{HOST}:{PORT}/countdown")
    print(f"AMLL lines endpoint: http://{HOST}:{PORT}/amll/lines.json")
    print("Press Ctrl-C to stop.")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
