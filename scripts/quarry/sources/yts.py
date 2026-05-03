"""YTS movie torrent source adapter."""
from __future__ import annotations
import urllib.parse
from .base import HTTPClient, SourceAdapter, _make_magnet
from ..common import compact_spaces, normalize_title, parse_quality_tags, quality_display_from_tags
from ..models import SearchIntent, SearchResult


class YTSSource(SourceAdapter):
    name = "yts"
    channel = "torrent"
    priority = 2

    def search(self, query: str, intent: SearchIntent, limit: int, page: int, http_client: HTTPClient) -> list[SearchResult]:
        url = "https://yts.bz/api/v2/list_movies.json?" + urllib.parse.urlencode(
            {"query_term": query, "limit": max(limit * 3, 20), "sort_by": "seeds"}
        )
        payload = http_client.get_json(url)
        if not isinstance(payload, dict):
            return []
        movies = payload.get("data", {}).get("movies") or []
        results: list[SearchResult] = []
        for movie in movies[: max(limit * 2, 10)]:
            title = normalize_title(movie.get("title_long") or movie.get("title") or "")
            for torrent in movie.get("torrents", []):
                info_hash = (torrent.get("hash") or "").lower()
                full_title = compact_spaces(
                    f"{title} {torrent.get('quality', '')} {torrent.get('type', '')} {torrent.get('video_codec', '')}"
                )
                quality_tags = parse_quality_tags(full_title)
                results.append(
                    SearchResult(
                        channel="torrent", normalized_channel="torrent",
                        source=self.name, upstream_source=self.name, provider="magnet",
                        title=full_title, link_or_magnet=_make_magnet(info_hash, full_title),
                        share_id_or_info_hash=info_hash,
                        size=torrent.get("size", ""),
                        seeders=int(torrent.get("seeds", 0)),
                        quality=quality_display_from_tags(quality_tags), quality_tags=quality_tags,
                        raw=torrent,
                    )
                )
        return results
