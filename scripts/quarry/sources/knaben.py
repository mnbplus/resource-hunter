"""Knaben Database torrent meta-search adapter (JSON API, zero-config).

Knaben aggregates results from 30+ public trackers (TPB, 1337x, TorrentGalaxy,
Nyaa, RARBG, etc.) via a single Elasticsearch-backed API.  No registration,
no API key, no login required — perfect fit for Quarry's zero-config philosophy.

API endpoint: POST https://api.knaben.org/v1
Docs: https://knaben.eu/api/v1
"""
from __future__ import annotations

import urllib.parse
from typing import Any

from .base import HTTPClient, SourceAdapter, _clean_magnet, _format_size, _make_magnet
from ..common import extract_share_id, normalize_title, parse_quality_tags, quality_display_from_tags
from ..models import SearchIntent, SearchResult

# Knaben category IDs (from their API docs)
_KIND_TO_CATEGORIES: dict[str, list[int]] = {
    "movie":    [5001000, 5004000],           # Movies, Movies HD
    "tv":       [5002000, 5005000],           # TV, TV HD
    "anime":    [5006000, 5001000],           # Anime, Movies
    "music":    [1000000, 1001000, 1002000],  # Audio, Audio MP3, Audio Lossless
    "software": [4000000, 4001000, 4002000],  # Applications, Apps Windows, Apps Mac
    "book":     [6000000, 6001000, 6002000],  # Other, Ebooks
}


class KnabenSource(SourceAdapter):
    name = "knaben"
    channel = "torrent"
    priority = 3  # backup/enhancement layer

    def search(self, query: str, intent: SearchIntent, limit: int, page: int, http_client: HTTPClient) -> list[SearchResult]:
        payload: dict[str, Any] = {
            "search_type": "100%",
            "search_field": "title",
            "query": query,
            "order_by": "seeders",
            "order_direction": "desc",
            "from": (page - 1) * limit,
            "size": min(limit * 2, 150),
            "hide_unsafe": True,
            "hide_xxx": True,
        }

        categories = _KIND_TO_CATEGORIES.get(intent.kind)
        if categories:
            payload["categories"] = categories

        try:
            response = http_client.post_json(
                "https://api.knaben.org/v1",
                json_data=payload,
                headers={"Content-Type": "application/json"},
                timeout=12,
            )
        except RuntimeError:
            return []

        if not isinstance(response, dict):
            return []

        hits = response.get("hits", [])
        if not isinstance(hits, list):
            return []

        results: list[SearchResult] = []
        for item in hits:
            title = normalize_title(item.get("title", ""))
            if not title:
                continue

            # Prefer magnetUrl, fall back to constructing from hash
            magnet = item.get("magnetUrl") or ""
            info_hash = (item.get("hash") or "").lower()

            if not magnet and info_hash:
                magnet = _make_magnet(info_hash, title)
            elif magnet:
                info_hash = extract_share_id(magnet, provider_hint="magnet") or info_hash

            if not magnet and not info_hash:
                continue

            seeders = int(item.get("seeders") or 0)
            size_str = _format_size(item.get("bytes"))
            quality_tags = parse_quality_tags(title)
            tracker = item.get("tracker", "")

            results.append(
                SearchResult(
                    channel="torrent", normalized_channel="torrent",
                    source=self.name, upstream_source=tracker or self.name,
                    provider="magnet",
                    title=title,
                    link_or_magnet=_clean_magnet(magnet) if magnet else _make_magnet(info_hash, title),
                    share_id_or_info_hash=info_hash,
                    size=size_str, seeders=seeders,
                    quality=quality_display_from_tags(quality_tags), quality_tags=quality_tags,
                    raw={"title": title, "seeders": seeders, "tracker": tracker,
                         "date": item.get("date", ""), "category": item.get("category", "")},
                )
            )

            if len(results) >= limit:
                break

        return results
