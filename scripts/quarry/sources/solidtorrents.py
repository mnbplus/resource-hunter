"""SolidTorrents / iTorrents torrent search adapter (JSON API, zero-config).

SolidTorrents (now also known as iTorrents.org) provides a public JSON API
for torrent search.  It indexes torrents from multiple sources and provides
verified magnet links.  No registration, no API key, no login required.

Replaces iDope in the original plan since iDope's site structure is
unreliable — SolidTorrents provides a cleaner JSON API with better stability.
"""
from __future__ import annotations

import html as html_mod
import re
import urllib.parse
from typing import Any

from .base import HTTPClient, SourceAdapter, _clean_magnet, _format_size, _make_magnet
from ..common import extract_share_id, normalize_title, parse_quality_tags, quality_display_from_tags
from ..exceptions import SourceNetworkError, SourceParseError
from ..models import SearchIntent, SearchResult

# SolidTorrents category mapping
_KIND_TO_CATEGORY: dict[str, str] = {
    "movie":    "Video",
    "tv":       "Video",
    "anime":    "Video",
    "music":    "Audio",
    "software": "App",
    "book":     "Other",
}


class SolidTorrentsSource(SourceAdapter):
    name = "solidtorrents"
    channel = "torrent"
    priority = 3  # backup/enhancement layer

    MIRRORS = (
        "solidtorrents.to",
        "solidtorrents.net",
        "solidtorrents.eu",
    )

    def search(self, query: str, intent: SearchIntent, limit: int, page: int, http_client: HTTPClient) -> list[SearchResult]:
        category = _KIND_TO_CATEGORY.get(intent.kind, "all")
        skip = (page - 1) * 20
        path = f"/api/v1/search?q={urllib.parse.quote(query)}&category={category}&skip={skip}&sort=seeders&order=desc&fuv=yes"

        try:
            response = http_client.get_json_with_mirrors(self.name, self.MIRRORS, path, timeout=10)
        except Exception:
            # Fallback to HTML scraping if API is down
            return self._scrape_fallback(query, page, limit, http_client)

        if not isinstance(response, dict):
            return self._scrape_fallback(query, page, limit, http_client)

        items = response.get("results", [])
        if not isinstance(items, list):
            return []

        results: list[SearchResult] = []
        for item in items:
            title = normalize_title(item.get("title", ""))
            if not title:
                continue

            magnet = item.get("magnet") or ""
            info_hash = (item.get("infohash") or "").lower()

            if not magnet and info_hash:
                magnet = _make_magnet(info_hash, title)
            elif magnet:
                info_hash = extract_share_id(magnet, provider_hint="magnet") or info_hash

            if not magnet and not info_hash:
                continue

            seeders = int(item.get("swarm", {}).get("seeders", 0) if isinstance(item.get("swarm"), dict) else item.get("seeders", 0))
            size_str = _format_size(item.get("size"))
            quality_tags = parse_quality_tags(title)

            results.append(
                SearchResult(
                    channel="torrent", normalized_channel="torrent",
                    source=self.name, upstream_source=self.name, provider="magnet",
                    title=title,
                    link_or_magnet=_clean_magnet(magnet) if magnet else _make_magnet(info_hash, title),
                    share_id_or_info_hash=info_hash,
                    size=size_str, seeders=seeders,
                    quality=quality_display_from_tags(quality_tags), quality_tags=quality_tags,
                    raw={"title": title, "seeders": seeders, "category": item.get("category", "")},
                )
            )

            if len(results) >= limit:
                break

        return results

    def _scrape_fallback(self, query: str, page: int, limit: int, http_client: HTTPClient) -> list[SearchResult]:
        """HTML scraping fallback when API is unavailable."""
        results: list[SearchResult] = []
        for mirror in self.MIRRORS:
            url = f"https://{mirror}/search?q={urllib.parse.quote(query)}&page={page}"
            try:
                html_text = http_client.get_text(url, timeout=10)
            except Exception:
                continue

            # Extract magnet links and nearby titles
            magnet_re = re.compile(r'href="(magnet:\?[^"]+)"', re.I)
            title_re = re.compile(r'<h5[^>]*>\s*<a[^>]*>([^<]+)</a>', re.I)

            titles = [normalize_title(html_mod.unescape(m.group(1))) for m in title_re.finditer(html_text)]
            magnets = [html_mod.unescape(m.group(1)) for m in magnet_re.finditer(html_text)]

            for title, raw_magnet in zip(titles, magnets):
                if not title:
                    continue
                info_hash = extract_share_id(raw_magnet, provider_hint="magnet")
                if not info_hash:
                    continue
                quality_tags = parse_quality_tags(title)
                results.append(
                    SearchResult(
                        channel="torrent", normalized_channel="torrent",
                        source=self.name, upstream_source=self.name, provider="magnet",
                        title=title, link_or_magnet=_clean_magnet(raw_magnet),
                        share_id_or_info_hash=info_hash,
                        size="", seeders=0,
                        quality=quality_display_from_tags(quality_tags), quality_tags=quality_tags,
                        raw={"title": title, "fallback": True},
                    )
                )
                if len(results) >= limit:
                    break
            if results:
                break

        return results
