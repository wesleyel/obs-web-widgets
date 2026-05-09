from __future__ import annotations

import bisect
import json
import re
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

NETEASE_SEARCH = "https://music.163.com/api/search/get/web"
NETEASE_LYRIC = "https://music.163.com/api/song/lyric"
NETEASE_LYRIC_V1 = "https://interface.music.163.com/api/song/lyric/v1"
HTTP_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X) obs-web-widgets",
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


def empty_bundle(message: str = "等待网易云音乐播放") -> LyricBundle:
    return LyricBundle([], None, message)


def http_json(url: str, params: dict[str, str]) -> dict[str, Any]:
    query = urllib.parse.urlencode(params)
    request = urllib.request.Request(f"{url}?{query}", headers=HTTP_HEADERS)
    with urllib.request.urlopen(request, timeout=8) as response:
        return json.loads(response.read().decode("utf-8"))


def normalize(value: str) -> str:
    value = value.lower()
    return re.sub(r"[\s\-_/|:：·・,，.。'\"“”‘’()（）\[\]【】]+", "", value)


def similarity(left: str, right: str) -> float:
    left = normalize(left)
    right = normalize(right)
    if not left or not right:
        return 0
    if left == right:
        return 1
    if left in right or right in left:
        return 0.86

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


def track_key(track: dict[str, Any]) -> str:
    return "|".join(
        [
            normalize(str(track.get("title") or "")),
            normalize(str(track.get("artist") or "")),
            str(round(float(track.get("duration") or 0))),
        ]
    )


def load_track_lyrics(track: dict[str, Any], lyrics_cache: dict[str, LyricBundle]) -> LyricBundle:
    title = str(track.get("title") or "").strip()
    artist = str(track.get("artist") or "").strip()
    duration = float(track.get("duration") or 0)
    key = track_key(track)

    if key in lyrics_cache:
        return lyrics_cache[key]

    if not title:
        result = empty_bundle()
        lyrics_cache[key] = result
        return result

    song_id = search_song_id(title, artist, duration)
    if song_id is None:
        result = empty_bundle("网易云未匹配到歌曲")
        lyrics_cache[key] = result
        return result

    result = fetch_lyrics(song_id)
    lyrics_cache[key] = result
    return result


def current_elapsed(track: dict[str, Any], *, lyric_offset: float) -> float:
    elapsed = float(track.get("elapsed") or 0)
    timestamp = float(track.get("timestamp") or track.get("capturedAt") or time.time())
    playback_rate = float(track.get("playbackRate") or 0)
    if playback_rate > 0:
        elapsed += (time.time() - timestamp) * playback_rate
    return max(0.0, elapsed + lyric_offset)


def lyric_snapshot(
    track: dict[str, Any],
    bundle: LyricBundle,
    *,
    lyric_offset: float,
    last_error: str,
) -> dict[str, Any]:
    elapsed = current_elapsed(track, lyric_offset=lyric_offset) if track else 0
    times = [line.at for line in bundle.lines]
    index = bisect.bisect_right(times, elapsed) - 1
    current = bundle.lines[index].text if 0 <= index < len(bundle.lines) else bundle.message
    previous = bundle.lines[index - 1].text if index > 0 else ""
    next_line = bundle.lines[index + 1].text if 0 <= index + 1 < len(bundle.lines) else ""

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
        "neteaseSongID": bundle.song_id,
        "amll": {
            "sourceFormat": bundle.source_format,
            "hasCoreLines": bool(bundle.lines),
            "hasYrc": bool(bundle.raw_yrc.strip()),
            "hasTranslation": bool(bundle.raw_translation_lrc.strip()),
            "hasRomanization": bool(bundle.raw_roman_lrc.strip()),
        },
        "lyric": {
            "previous": previous,
            "current": current,
            "next": next_line,
            "lineCount": len(bundle.lines),
        },
        "message": bundle.message,
        "error": last_error,
        "updatedAt": time.time(),
    }


def timed_text_by_millisecond(raw_lrc: str) -> dict[int, str]:
    result: dict[int, str] = {}
    for line in parse_lrc(raw_lrc):
        text = line.text.strip()
        if text:
            result[round(line.at * 1000)] = text
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


def parse_yrc_core_lines(
    raw_yrc: str,
    translations: dict[int, str],
    romanizations: dict[int, str],
) -> list[dict[str, Any]]:
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
        start = round(line.at * 1000)
        end = round(line_end_at(index, bundle.lines, duration) * 1000)
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


def amll_lines_payload(track: dict[str, Any], bundle: LyricBundle) -> dict[str, Any]:
    return {
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
    }
