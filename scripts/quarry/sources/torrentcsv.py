"""TorrentCSV — DHT network torrent search via public JSON API (zero-config).

TorrentCSV is an open-source torrent search engine that indexes the DHT network.
It provides a clean JSON API with no authentication required.

API endpoint: GET https://torrents-csv.com/service/search?q={query}&size={limit}&page={page}

Response format:
  {
    "torrents": [
      {
        "name": "...",
        "size_bytes": 1234567890,
        "seeders": 42,
        "leechers": 10,
        "infohash": "abc123...",
        "created_unix": 1700000000,
        "scraped_date": 1700001000
      }
    ]
  }
"""
from __future__ import annotations

import urllib.parse
from typing import Any

from .base import HTTPClient, SourceAdapter, _clean_magnet, _format_size, _make_magnet
from ..common import extract_share_id, normalize_title, parse_quality_tags, quality_display_from_tags
from ..exceptions import SourceNetworkError, SourceParseError
from ..models import SearchIntent, SearchResult


class TorrentCSVSource(SourceAdapter):
    """TorrentCSV — open-source DHT torrent search with clean JSON API."""
    name = "torrentcsv"
    channel = "torrent"
    priority = 3

    def search(
        self,
        query: str,
        intent: SearchIntent,
        limit: int,
        page: int,
        http_client: HTTPClient,
    ) -> list[SearchResult]:
        params = urllib.parse.urlencode({
            "q": query,
            "size": min(limit * 2, 100),
            "page": page,
        })
        url = f"https://torrents-csv.com/service/search?{params}"

        try:
            response = http_client.get_json(url, timeout=10)
        except Exception as exc:
            raise SourceNetworkError(str(exc), source=self.name, url=url) from exc

        if not isinstance(response, dict):
            raise SourceParseError("unexpected response type", source=self.name, url=url)

        torrents = response.get("torrents", [])
        if not isinstance(torrents, list):
            raise SourceParseError("torrents field is not a list", source=self.name, url=url)

        results: list[SearchResult] = []
        for item in torrents:
            if not isinstance(item, dict):
                continue

            title = normalize_title(item.get("name", ""))
            if not title:
                continue

            info_hash = (item.get("infohash") or "").lower()
            if not info_hash:
                continue

            seeders = int(item.get("seeders") or 0)
            leechers = int(item.get("leechers") or 0)
            size_str = _format_size(item.get("size_bytes"))

            magnet = _make_magnet(info_hash, title)
            quality_tags = parse_quality_tags(title)

            results.append(
                SearchResult(
                    channel="torrent",
                    normalized_channel="torrent",
                    source=self.name,
                    upstream_source=self.name,
                    provider="magnet",
                    title=title,
                    link_or_magnet=magnet,
                    share_id_or_info_hash=info_hash,
                    size=size_str,
                    seeders=seeders,
                    quality=quality_display_from_tags(quality_tags),
                    quality_tags=quality_tags,
                    raw={
                        "leechers": leechers,
                        "created_unix": item.get("created_unix"),
                        "scraped_date": item.get("scraped_date"),
                    },
                )
            )

            if len(results) >= limit:
                break

        return results
