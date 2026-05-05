"""ThePirateBay (apibay) torrent source adapter."""
from __future__ import annotations
import hashlib
import urllib.parse
from .base import HTTPClient, SourceAdapter, _format_size, _make_magnet
from ..common import normalize_title, parse_quality_tags, quality_display_from_tags
from ..models import SearchIntent, SearchResult


class TPBSource(SourceAdapter):
    name = "tpb"
    channel = "torrent"
    priority = 2

    MIRRORS = (
        "apibay.org",
    )

    def search(self, query: str, intent: SearchIntent, limit: int, page: int, http_client: HTTPClient) -> list[SearchResult]:
        path = f"/q.php?q={urllib.parse.quote(query)}&cat=0"
        payload = http_client.get_json_with_mirrors(self.name, self.MIRRORS, path)
        if not isinstance(payload, list):
            return []
        results: list[SearchResult] = []
        for item in payload[: max(limit * 3, 12)]:
            name = normalize_title(item.get("name", ""))
            if not name or name == "No results returned":
                continue
            info_hash = (item.get("info_hash") or "").lower()
            quality_tags = parse_quality_tags(name)
            results.append(
                SearchResult(
                    channel="torrent", normalized_channel="torrent",
                    source=self.name, upstream_source=self.name, provider="magnet",
                    title=name, link_or_magnet=_make_magnet(info_hash, name),
                    share_id_or_info_hash=info_hash or hashlib.sha1(name.encode("utf-8")).hexdigest(),
                    size=_format_size(item.get("size", 0)),
                    seeders=int(item.get("seeders", 0)),
                    quality=quality_display_from_tags(quality_tags), quality_tags=quality_tags,
                    raw=item,
                )
            )
        return results
