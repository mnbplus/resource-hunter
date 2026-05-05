"""LimeTorrents search source adapter (RSS/XML)."""
from __future__ import annotations
import re
import urllib.parse
from xml.etree import ElementTree
from .base import HTTPClient, SourceAdapter, _make_magnet
from ..common import extract_share_id, normalize_title, parse_quality_tags, quality_display_from_tags
from ..exceptions import SourceNetworkError, SourceParseError
from ..models import SearchIntent, SearchResult


_SEED_RE = re.compile(r"Seeds:\s*(\d+)", re.IGNORECASE)
_HASH_RE = re.compile(r"/torrent/([A-Fa-f0-9]{40})\.torrent", re.IGNORECASE)


class LimeTorrentsSource(SourceAdapter):
    name = "limetorrents"
    channel = "torrent"
    priority = 3

    MIRRORS = (
        "www.limetorrents.lol",
        "www.limetorrents.fun",
        "www.limetorrents.cc",
    )

    def search(self, query: str, intent: SearchIntent, limit: int, page: int, http_client: HTTPClient) -> list[SearchResult]:
        encoded = urllib.parse.quote(query)
        path = f"/searchrss/{encoded}/"
        try:
            payload = http_client.get_text_with_mirrors(self.name, self.MIRRORS, path)
        except Exception as exc:
            raise SourceNetworkError(str(exc), source=self.name) from exc

        if not payload or len(payload) < 100:
            raise SourceParseError("RSS feed empty or too short", source=self.name)

        try:
            root = ElementTree.fromstring(payload)
        except ElementTree.ParseError:
            return []

        results: list[SearchResult] = []
        for item in root.findall("./channel/item")[: max(limit * 3, 15)]:
            title = normalize_title(item.findtext("title", ""))
            if not title:
                continue

            # Extract info hash from enclosure .torrent URL
            enclosure = item.find("enclosure")
            torrent_url = enclosure.get("url", "") if enclosure is not None else ""
            hash_match = _HASH_RE.search(torrent_url)
            info_hash = hash_match.group(1).lower() if hash_match else ""
            if not info_hash:
                continue

            magnet = _make_magnet(info_hash, title)

            # Parse seeders from description like "Seeds: 39 , Leechers 17"
            desc = item.findtext("description", "")
            seed_match = _SEED_RE.search(desc)
            seeders = int(seed_match.group(1)) if seed_match else 0

            # Size from <size> tag (bytes)
            size_bytes = item.findtext("size", "")
            size_str = _format_bytes(int(size_bytes)) if size_bytes and size_bytes.isdigit() else ""

            category = item.findtext("category", "")
            quality_tags = parse_quality_tags(title)

            results.append(
                SearchResult(
                    channel="torrent", normalized_channel="torrent",
                    source=self.name, upstream_source=self.name, provider="magnet",
                    title=title, link_or_magnet=magnet,
                    share_id_or_info_hash=info_hash,
                    size=size_str,
                    seeders=seeders,
                    quality=quality_display_from_tags(quality_tags), quality_tags=quality_tags,
                    raw={"title": title, "seeders": seeders, "category": category},
                )
            )
        return results


def _format_bytes(n: int | float) -> str:
    """Convert bytes to human-readable size string."""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1024
    return f"{n:.1f} PB"
