"""Backward-compatible re-export layer.

All public names previously importable from ``quarry.common``
remain available here. New code should prefer the specific sub-modules:

- ``parsers``   – release tag / quality parsing
- ``url_utils`` – URL, provider, platform detection
- ``text_utils`` – title normalization, tokenization, language detection
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

# Re-export from parsers
from .parsers import (
    AUDIO_CODEC_PATTERNS,
    BOOK_FORMAT_RE,
    HDR_PATTERNS,
    LOSSLESS_TERMS,
    PACK_PATTERNS,
    QUALITY_RESOLUTION_RE,
    RELEASE_NOISE_RE,
    SEASON_EPISODE_RE,
    SOURCE_PATTERNS,
    SUBTITLE_TERMS,
    VERSION_RE,
    VIDEO_CODEC_PATTERNS,
    YEAR_RE,
    extract_book_formats,
    extract_season_episode,
    extract_versions,
    extract_year,
    infer_quality,
    parse_quality_tags,
    parse_release_tags,
    quality_display_from_tags,
)

# Re-export from url_utils
from .url_utils import (
    DOMAIN_PROVIDER_MAP,
    PLATFORM_MAP,
    VIDEO_URL_HINTS,
    clean_share_url,
    detect_platform,
    extract_password,
    extract_share_id,
    infer_provider_from_url,
    is_video_url,
)

# Re-export from text_utils
from .text_utils import (
    BRACKET_RE,
    CHINESE_RE,
    EN_ALIAS_PAREN_RE,
    EN_ALIAS_RE,
    LATIN_RE,
    STOPWORDS,
    TOKEN_RE,
    compact_spaces,
    detect_language_mix,
    extract_chinese_alias,
    extract_english_alias,
    has_chinese,
    has_latin,
    normalize_key,
    normalize_title,
    text_contains_any,
    title_core,
    title_tokens,
    token_overlap_score,
    unique_preserve,
)

# --- Category term lists (kept here as they are used across multiple modules) ---

ANIME_TERMS = (
    "\u52a8\u6f2b",
    "\u52a8\u753b",
    "\u756a\u5267",
    "\u65b0\u756a",
    "anime",
    "ova",
    "nyaa",
    "attack on titan",
    "one piece",
    "naruto",
    "demon slayer",
    "\u8fdb\u51fb",
    "\u5de8\u4eba",
    "\u6d77\u8d3c",
    "\u706b\u5f71",
)
TV_TERMS = (
    "season",
    "episode",
    "series",
    "\u7f8e\u5267",
    "\u82f1\u5267",
    "\u97e9\u5267",
    "\u65e5\u5267",
    "\u7b2c\u5b63",
    "\u7b2c\u96c6",
)
MUSIC_TERMS = (
    "\u97f3\u4e50",
    "\u4e13\u8f91",
    "\u5355\u66f2",
    "album",
    "single",
    "soundtrack",
    "ost",
    "flac",
    "mp3",
    "aac",
    "\u65e0\u635f",
)
SOFTWARE_TERMS = (
    "\u8f6f\u4ef6",
    "\u7a0b\u5e8f",
    "\u5de5\u5177",
    "\u5ba2\u6237\u7aef",
    "portable",
    "apk",
    "installer",
    ".exe",
    ".dmg",
    ".msi",
    "windows",
    "mac",
    "linux",
)
SOFTWARE_BRANDS = (
    "adobe",
    "photoshop",
    "illustrator",
    "premiere",
    "after effects",
    "windows",
    "office",
    "visual studio",
    "jetbrains",
    "pycharm",
    "intellij",
    "autocad",
)
BOOK_TERMS = (
    "epub",
    "pdf",
    "mobi",
    "azw3",
    "txt",
    "\u7535\u5b50\u4e66",
    "\u5c0f\u8bf4",
    "\u7f51\u6587",
    "\u8f7b\u5c0f\u8bf4",
    "\u6f2b\u753b",
    "manga",
    "comic",
    "ebook",
    "novel",
    "light novel",
    "audiobook",
    "\u6709\u58f0\u4e66",
)


# --- Functions that remain in common (infrastructure / detection) ---


def ensure_utf8_stdio() -> None:
    for handle_name in ("stdout", "stderr"):
        handle = getattr(sys, handle_name, None)
        if handle is not None and hasattr(handle, "reconfigure"):
            handle.reconfigure(encoding="utf-8", errors="replace")


def storage_root() -> Path:
    workspace = Path(os.environ.get("OPENCLAW_WORKSPACE", Path.home() / ".openclaw" / "workspace"))
    root = workspace / "storage" / "quarry"
    root.mkdir(parents=True, exist_ok=True)
    return root


def default_download_dir() -> Path:
    downloads = storage_root() / "downloads"
    downloads.mkdir(parents=True, exist_ok=True)
    return downloads


def dump_json(data: object) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2, sort_keys=False)


def safe_filename(name: str) -> str:
    value = re.sub(r'[\\/:*?"<>|]+', "_", name).strip()
    return value or "download"


def source_priority(source_name: str) -> int:
    priorities = {
        "upyunso": 1,
        "pansou": 1,
        "ps.252035": 1,
        "torznab": 1,
        "panhunt": 2,

        "nyaa": 1,
        "dmhy": 1,
        "bangumi_moe": 1,
        "eztv": 1,
        "torrentgalaxy": 2,
        "bitsearch": 2,
        "tpb": 2,
        "yts": 2,
        "1337x": 3,
        "limetorrents": 3,
        "torlock": 3,
        "fitgirl": 3,
        "torrentmac": 3,
        "ext_to": 3,
        "annas": 2,
        "subsplease": 1,
        # DHT backup sources
        "knaben": 3,
        "btdig": 4,
        "solidtorrents": 4,
        # Book / DHT sources
        "libgen": 2,
        "torrentcsv": 3,
        "glodls": 4,
        "idope": 4,
    }
    return priorities.get((source_name or "").lower(), 9)


def detect_kind(text: str, explicit_kind: str | None = None) -> str:
    if explicit_kind:
        return explicit_kind
    lowered = (text or "").lower()
    if is_video_url(lowered):
        return "video"
    if lowered.startswith("magnet:") or lowered.endswith(".torrent"):
        return "torrent"
    season, episode = extract_season_episode(lowered)
    if season is not None or episode is not None:
        return "tv"
    if any(term in lowered for term in ANIME_TERMS):
        return "anime"
    if any(term in lowered for term in MUSIC_TERMS) or any(term in lowered for term in LOSSLESS_TERMS):
        return "music"
    if any(term in lowered for term in SOFTWARE_TERMS) or any(term in lowered for term in SOFTWARE_BRANDS):
        return "software"
    if any(term in lowered for term in BOOK_TERMS):
        return "book"
    if any(term in lowered for term in TV_TERMS):
        return "tv"
    if extract_year(lowered):
        return "movie"
    return "general"
