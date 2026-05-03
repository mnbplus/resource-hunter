from __future__ import annotations

import hashlib
import html
import json
import re
import urllib.parse
from dataclasses import replace
from typing import Any

from .cache import ResourceCache
from .common import (
    BOOK_TERMS,
    LOSSLESS_TERMS,
    compact_spaces,
    detect_kind,
    detect_language_mix,
    extract_book_formats,
    extract_chinese_alias,
    extract_english_alias,
    extract_season_episode,
    extract_versions,
    extract_year,
    has_chinese,
    is_video_url,
    title_core,
    title_tokens,
    unique_preserve,
)
from .models import SearchIntent, SearchPlan
from .sources import HTTPClient


class AliasResolver:
    SEARCH_RESULT_RE = re.compile(
        r'<a[^>]+class="result__a"[^>]+href="(?P<href>[^"]+)"[^>]*>(?P<title>.*?)</a>',
        re.I | re.S,
    )
    META_CONTENT_RE = re.compile(
        r'<meta[^>]+(?:property|name)=["\'](?:og:title|title|description|og:description|twitter:title)["\'][^>]+content=["\'](?P<content>[^"\']+)["\']',
        re.I,
    )
    HTML_TITLE_RE = re.compile(r"<title>(?P<title>.*?)</title>", re.I | re.S)
    JSON_LD_RE = re.compile(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(?P<json>.*?)</script>', re.I | re.S)
    ENGLISH_TITLE_RE = re.compile(r"\b[A-Z][A-Za-z0-9'&:\-]+(?: [A-Z0-9][A-Za-z0-9'&:\-]+){0,8}\b")
    BLACKLIST = {
        "youtube",
        "bilibili",
        "douban",
        "baidu",
        "wikipedia",
        "letterboxd",
        "imdb",
        "sohu",
        "tencent",
        "video",
        "cast",
        "name",
        "rate",
        "full",
        "movie",
        "film",
    }

    def search_results(self, query: str, http_client: HTTPClient) -> list[dict[str, str]]:
        url = "https://duckduckgo.com/html/?" + urllib.parse.urlencode({"q": query})
        html_text = http_client.get_text(url, timeout=12)
        results: list[dict[str, str]] = []
        for match in self.SEARCH_RESULT_RE.finditer(html_text):
            href = html.unescape(match.group("href"))
            parsed = urllib.parse.urlparse(href)
            if "duckduckgo.com" in parsed.netloc and "uddg=" in href:
                href = urllib.parse.parse_qs(parsed.query).get("uddg", [href])[0]
            title = compact_spaces(re.sub(r"<[^>]+>", " ", html.unescape(match.group("title"))))
            if href.startswith("http"):
                results.append({"title": title, "url": href})
            if len(results) >= 5:
                break
        return results

    def fetch_metadata_texts(self, url: str, http_client: HTTPClient) -> list[str]:
        html_text = http_client.get_text(url, timeout=12)
        texts: list[str] = [url]
        title_match = self.HTML_TITLE_RE.search(html_text)
        if title_match:
            texts.append(html.unescape(re.sub(r"<[^>]+>", " ", title_match.group("title"))))
        for meta_match in self.META_CONTENT_RE.finditer(html_text):
            texts.append(html.unescape(meta_match.group("content")))
        for json_match in self.JSON_LD_RE.finditer(html_text):
            raw_json = html.unescape(json_match.group("json")).strip()
            try:
                payload = json.loads(raw_json)
            except Exception:
                continue
            candidates = payload if isinstance(payload, list) else [payload]
            for item in candidates:
                if not isinstance(item, dict):
                    continue
                for key in ("name", "alternateName", "headline"):
                    value = item.get(key)
                    if isinstance(value, str):
                        texts.append(value)
                    elif isinstance(value, list):
                        texts.extend([entry for entry in value if isinstance(entry, str)])
        return [compact_spaces(text) for text in texts if compact_spaces(text)]

    def _extract_aliases_from_texts(self, texts: list[str], year: str) -> tuple[str, list[str]]:
        alternates: list[str] = []
        for text in texts:
            for match in self.ENGLISH_TITLE_RE.findall(text):
                candidate = compact_spaces(match)
                lowered = candidate.lower()
                if lowered in self.BLACKLIST:
                    continue
                if year and year in candidate:
                    candidate = compact_spaces(candidate.replace(year, " "))
                if len(candidate) < 4:
                    continue
                alternates.append(candidate)
        ranked = sorted(
            unique_preserve(alternates),
            key=lambda candidate: (
                0 if len(candidate.split()) >= 2 else 1,
                len(candidate),
            ),
        )
        english = ranked[0] if ranked else ""
        return english, ranked[:8]

    def resolve(self, intent: SearchIntent, cache: ResourceCache, http_client: HTTPClient) -> dict[str, Any]:
        if intent.kind not in {"movie", "general", "tv", "anime"} or not intent.chinese_title_core or intent.english_alias:
            return {}
        year_part = f" {intent.year}" if intent.year else ""
        cache_key = hashlib.sha256(f"alias_v9|{intent.chinese_title_core}|{intent.year}|{intent.kind}".encode("utf-8")).hexdigest()
        cached = cache.get_alias_resolution(cache_key)
        if cached:
            return cached

        resolver_sources: list[str] = []
        collected_texts: list[str] = []
        if intent.kind == "anime":
            search_queries = [
                f"{intent.chinese_title_core}{year_part} anime",
                f"{intent.chinese_title_core}{year_part} myanimelist",
                f"{intent.chinese_title_core}{year_part} anilist",
            ]
        elif intent.kind == "tv":
            search_queries = [
                f"{intent.chinese_title_core}{year_part} tv series",
                f"{intent.chinese_title_core}{year_part} imdb",
                f"{intent.chinese_title_core}{year_part} tvdb",
            ]
        else:
            search_queries = [
                f"{intent.chinese_title_core}{year_part} movie",
                f"{intent.chinese_title_core}{year_part} imdb",
                f"{intent.chinese_title_core}{year_part} letterboxd",
            ]
        for query in search_queries:
            try:
                result_items = self.search_results(query, http_client)
            except Exception:
                continue
            for item in result_items[:4]:
                resolver_sources.append(item["url"])
                collected_texts.append(item["title"])
                try:
                    collected_texts.extend(self.fetch_metadata_texts(item["url"], http_client))
                except Exception:
                    continue

        english_title, alternates = self._extract_aliases_from_texts(collected_texts, intent.year)
        payload = {
            "original_title": intent.chinese_title_core,
            "english_title": english_title,
            "romanized_title": "",
            "alternate_titles": alternates,
            "resolved_year": intent.year,
            "resolver_sources": unique_preserve(resolver_sources),
        }
        cache.set_alias_resolution(cache_key, payload, ttl_seconds=86400)
        return payload


def parse_intent(
    query: str,
    explicit_kind: str | None = None,
    channel: str = "both",
    quick: bool = False,
    wants_sub: bool = False,
    wants_4k: bool = False,
) -> SearchIntent:
    season, episode = extract_season_episode(query)
    english_alias = extract_english_alias(query)
    chinese_alias = extract_chinese_alias(query)
    kind = detect_kind(query, explicit_kind)
    query_title_core = title_core(query) or title_core(english_alias) or title_core(chinese_alias)
    return SearchIntent(
        query=compact_spaces(query),
        original_query=query,
        kind=kind,
        channel=channel,
        english_alias=english_alias,
        chinese_alias=chinese_alias,
        year=extract_year(query),
        season=season,
        episode=episode,
        wants_sub=wants_sub,
        wants_4k=wants_4k,
        quick=quick,
        is_video_url=is_video_url(query),
        title_core=query_title_core,
        title_tokens=title_tokens(query_title_core or query),
        english_title_core=title_core(english_alias),
        chinese_title_core=title_core(chinese_alias),
        resolved_titles=[],
        resolved_year="",
        alias_resolution={},
        language_mix=detect_language_mix(query),
        format_hints=extract_book_formats(query),
        version_hints=extract_versions(query),
    )


def enrich_intent_with_aliases(intent: SearchIntent, alias_resolution: dict[str, Any]) -> SearchIntent:
    if not alias_resolution:
        return intent
    resolved_titles = unique_preserve(
        [
            alias_resolution.get("english_title", ""),
            alias_resolution.get("romanized_title", ""),
            *alias_resolution.get("alternate_titles", []),
        ]
    )
    english_alias = intent.english_alias or alias_resolution.get("english_title", "")
    resolved_year = alias_resolution.get("resolved_year") or intent.year
    return replace(
        intent,
        kind="movie" if intent.kind == "general" else intent.kind,
        english_alias=english_alias,
        english_title_core=title_core(english_alias),
        resolved_titles=resolved_titles,
        resolved_year=resolved_year,
        alias_resolution=alias_resolution,
    )


def _episode_variants(intent: SearchIntent) -> list[str]:
    if intent.season is None and intent.episode is None:
        return []
    parts: list[str] = []
    if intent.season is not None and intent.episode is not None:
        parts.append(f"S{intent.season:02d}E{intent.episode:02d}")
        parts.append(f"Season {intent.season} Episode {intent.episode}")
        parts.append(f"\u7b2c{intent.season}\u5b63 \u7b2c{intent.episode}\u96c6")
    elif intent.season is not None:
        parts.append(f"Season {intent.season}")
        parts.append(f"\u7b2c{intent.season}\u5b63")
    elif intent.episode is not None:
        parts.append(f"Episode {intent.episode}")
        parts.append(f"\u7b2c{intent.episode}\u96c6")
    return parts


def _movie_variants(intent: SearchIntent) -> list[str]:
    variants = [intent.query, intent.title_core, intent.english_alias, intent.chinese_alias, *intent.resolved_titles]
    year = intent.resolved_year or intent.year
    if year:
        variants.extend(
            [
                compact_spaces(f"{intent.title_core or intent.query} {year}"),
                compact_spaces(f"{intent.english_alias or intent.title_core or intent.query} {year}"),
            ]
        )
    if intent.wants_4k:
        variants.extend(
            [
                compact_spaces(f"{intent.title_core or intent.query} 2160p"),
                compact_spaces(f"{intent.title_core or intent.query} 4K"),
            ]
        )
    return unique_preserve([item for item in variants if item])


def _tv_variants(intent: SearchIntent) -> list[str]:
    episode_variants = _episode_variants(intent)
    bases = [intent.query, intent.title_core, intent.english_alias, intent.chinese_alias]
    variants: list[str] = list(bases)
    for base in [item for item in bases if item]:
        for suffix in episode_variants:
            variants.append(compact_spaces(f"{base} {suffix}"))
    return unique_preserve([item for item in variants if item])


def _anime_variants(intent: SearchIntent) -> list[str]:
    variants = _tv_variants(intent)
    variants.extend([compact_spaces(f"{intent.query} anime"), compact_spaces(f"{intent.title_core or intent.query} nyaa")])
    if intent.wants_sub:
        variants.append(compact_spaces(f"{intent.title_core or intent.query} subtitles"))
    return unique_preserve([item for item in variants if item])


def _music_variants(intent: SearchIntent) -> list[str]:
    variants = [intent.query, intent.title_core, intent.english_alias, intent.chinese_alias]
    base = intent.title_core or intent.query
    # Core lossless format variants
    variants.extend([
        compact_spaces(f"{base} flac"),
        compact_spaces(f"{base} lossless"),
        compact_spaces(f"{base} \u65e0\u635f"),
    ])
    # Hi-Res / DSD variants for audiophile searches
    lowered = intent.original_query.lower()
    if any(term in lowered for term in ("hi-res", "hires", "dsd", "24bit", "\u65e0\u635f")):
        variants.extend([
            compact_spaces(f"{base} Hi-Res"),
            compact_spaces(f"{base} DSD"),
            compact_spaces(f"{base} 24bit"),
        ])
    # Chinese-specific: add album/discography suffixes
    if has_chinese(intent.query):
        variants.extend([
            compact_spaces(f"{base} \u4e13\u8f91"),
            compact_spaces(f"{base} wav"),
        ])
    return unique_preserve([item for item in variants if item])


def _software_variants(intent: SearchIntent) -> list[str]:
    variants = [intent.query, intent.title_core, intent.english_alias]
    for version in intent.version_hints[:2]:
        variants.append(compact_spaces(f"{intent.title_core or intent.query} {version}"))
    variants.extend(
        [
            compact_spaces(f"{intent.title_core or intent.query} windows"),
            compact_spaces(f"{intent.title_core or intent.query} mac"),
        ]
    )
    return unique_preserve([item for item in variants if item])


def _book_variants(intent: SearchIntent) -> list[str]:
    variants = [intent.query, intent.title_core, intent.english_alias, intent.chinese_alias]
    base = intent.title_core or intent.query
    variants.extend([
        compact_spaces(f"{base} pdf"),
        compact_spaces(f"{base} epub"),
        compact_spaces(f"{base} txt"),
    ])
    # For Chinese queries, also add "小说" suffix which is common in pan search
    if has_chinese(intent.query):
        variants.append(compact_spaces(f"{base} \u5c0f\u8bf4"))
    return unique_preserve([item for item in variants if item])


def build_plan(intent: SearchIntent) -> SearchPlan:
    if intent.is_video_url:
        return SearchPlan(channels=["video"], notes=["url routed to video pipeline"])

    if intent.channel == "pan":
        channels = ["pan"]
    elif intent.channel == "torrent":
        channels = ["torrent"]
    elif intent.kind in {"anime", "tv"}:
        channels = ["torrent", "pan"]
    else:
        channels = ["pan", "torrent"]

    if intent.kind == "movie":
        pan_queries = torrent_queries = _movie_variants(intent)
    elif intent.kind == "tv":
        pan_queries = torrent_queries = _tv_variants(intent)
    elif intent.kind == "anime":
        pan_queries = torrent_queries = _anime_variants(intent)
    elif intent.kind == "music":
        pan_queries = _music_variants(intent)
        torrent_queries = _music_variants(intent)
    elif intent.kind == "software":
        pan_queries = _software_variants(intent)
        torrent_queries = _software_variants(intent)
    elif intent.kind == "book":
        pan_queries = _book_variants(intent)
        torrent_queries = _book_variants(intent)
    else:
        pan_queries = torrent_queries = unique_preserve(
            [
                intent.query,
                intent.title_core,
                intent.english_alias,
                intent.chinese_alias,
                *intent.resolved_titles,
            ]
        )

    notes: list[str] = []
    if intent.kind == "anime":
        preferred_pan_sources = ["upyunso", "pansou", "ps.252035", "panhunt"]
        preferred_torrent_sources = ["torznab", "nyaa", "dmhy", "bangumi_moe", "bitsearch", "torrentgalaxy", "tpb", "1337x", "limetorrents", "torlock", "ext_to", "eztv", "yts"]
        notes.append("anime uses bilingual variants and nyaa-first routing")
    elif intent.kind == "tv":
        preferred_pan_sources = ["upyunso", "pansou", "ps.252035", "panhunt"]
        preferred_torrent_sources = ["torznab", "eztv", "torrentgalaxy", "bitsearch", "tpb", "1337x", "limetorrents", "torlock", "ext_to", "dmhy", "nyaa", "yts"]
        notes.append("tv uses strict season-episode routing before pan supplements")
    elif intent.kind == "movie":
        preferred_pan_sources = ["upyunso", "pansou", "ps.252035", "panhunt"]
        preferred_torrent_sources = ["torznab", "yts", "torrentgalaxy", "bitsearch", "tpb", "1337x", "limetorrents", "torlock", "ext_to", "eztv", "nyaa"]
        notes.append("movie uses title-family plus year variants")
    elif intent.kind == "software":
        preferred_pan_sources = ["upyunso", "pansou", "ps.252035", "panhunt"]
        preferred_torrent_sources = ["torznab", "torrentmac", "fitgirl", "torrentgalaxy", "bitsearch", "tpb", "1337x", "limetorrents", "torlock", "ext_to", "nyaa"]
        notes.append("software/games uses torrentmac, fitgirl and general trackers")
    elif intent.kind == "music":
        preferred_pan_sources = ["upyunso", "pansou", "ps.252035", "panhunt"]
        # Music: remove high-noise sources (tpb returns adult content for artist names)
        preferred_torrent_sources = ["torznab", "nyaa", "dmhy", "bitsearch", "1337x", "torrentgalaxy", "ext_to"]
        notes.append("music uses pan-first routing with noise-filtered torrent sources")
    elif intent.kind == "book":
        preferred_pan_sources = ["upyunso", "pansou", "ps.252035", "panhunt"]
        preferred_torrent_sources = ["annas", "torznab", "bitsearch", "1337x", "limetorrents", "torrentgalaxy", "torlock", "ext_to", "tpb"]
        notes.append("book uses annas-archive-first routing with format-aware variants")
    else:
        preferred_pan_sources = ["upyunso", "pansou", "ps.252035", "panhunt"]
        preferred_torrent_sources = ["torznab", "torrentgalaxy", "bitsearch", "tpb", "1337x", "limetorrents", "torlock", "ext_to", "fitgirl", "dmhy", "nyaa", "eztv", "yts"]
        notes.append("non-video categories expand format/version variants before torrent fallback")

    if intent.wants_sub:
        notes.append("subtitle preference enabled")
    if intent.wants_4k:
        notes.append("4k preference enabled")
    if intent.language_mix == "mixed":
        notes.append("mixed-language query detected")
    if has_chinese(intent.query) and intent.kind == "movie" and intent.year and not intent.english_alias:
        notes.append("alias resolver eligible")

    return SearchPlan(
        channels=channels,
        pan_queries=unique_preserve([item for item in pan_queries if item]),
        torrent_queries=unique_preserve([item for item in torrent_queries if item]),
        preferred_pan_sources=preferred_pan_sources,
        preferred_torrent_sources=preferred_torrent_sources,
        notes=notes,
    )


__all__ = [
    "AliasResolver",
    "build_plan",
    "enrich_intent_with_aliases",
    "parse_intent",
]
