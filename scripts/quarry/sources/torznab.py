"""Torznab API search source adapter (for Jackett / Prowlarr)."""
from __future__ import annotations
import os
import urllib.parse
from xml.etree import ElementTree
from .base import HTTPClient, SourceAdapter, _clean_magnet
from ..common import extract_share_id, normalize_title, parse_quality_tags, quality_display_from_tags
from ..models import SearchIntent, SearchResult


class TorznabSource(SourceAdapter):
    name = "torznab"
    channel = "torrent"
    priority = 1

    def search(self, query: str, intent: SearchIntent, limit: int, page: int, http_client: HTTPClient) -> list[SearchResult]:
        api_url = os.environ.get("TORZNAB_URL", "").strip()
        api_key = os.environ.get("TORZNAB_APIKEY", "").strip()
        
        if not api_url or not api_key:
            # Skip gracefully if not configured, this is an optional self-hosted indexing service
            return []
            
        # Torznab pagination uses offset instead of page, but we'll stick to basic search for now
        # since most instances return plenty of results. limit is mostly ignored by the server.
        offset = (page - 1) * limit
        
        url = f"{api_url}?t=search&q={urllib.parse.quote(query)}&apikey={api_key}&offset={offset}&limit={limit * 2}"
        try:
            payload = http_client.get_text(url)
        except Exception as exc:
            raise RuntimeError(f"torznab request failed: {exc}") from exc
            
        try:
            root = ElementTree.fromstring(payload)
        except ElementTree.ParseError as exc:
            raise RuntimeError(f"torznab invalid XML: {exc}") from exc

        # Check for Torznab error
        error_node = root.find("error")
        if error_node is not None:
            raise RuntimeError(f"torznab server error: {error_node.get('description', 'unknown')}")

        results: list[SearchResult] = []
        for item in root.findall("./channel/item")[: max(limit * 3, 20)]:
            title = normalize_title(item.findtext("title", ""))
            if not title:
                continue

            # In Torznab, the link tag or enclosure is usually the magnet or .torrent file
            magnet = ""
            enclosure = item.find("enclosure")
            if enclosure is not None and enclosure.get("url"):
                magnet = enclosure.get("url", "")
            if not magnet:
                magnet = item.findtext("link", "")
                
            if not magnet:
                continue

            # Parse torznab specific attributes
            seeders = 0
            size_bytes = 0
            indexer_name = "torznab"
            
            for attr in item.findall("{http://torznab.com/schemas/2015/feed}attr"):
                attr_name = attr.get("name")
                attr_value = attr.get("value")
                if attr_name == "seeders" and attr_value and attr_value.isdigit():
                    seeders = int(attr_value)
                elif attr_name == "size" and attr_value and attr_value.isdigit():
                    size_bytes = int(attr_value)
                    
            # Jackett often injects indexer name in description or title, but standard torznab doesn't guarantee it.
            # We'll use the channel title if available.
            channel_title = root.findtext("./channel/title", "torznab")
            if channel_title != "torznab":
                indexer_name = f"torznab:{channel_title.lower().replace(' ', '')}"
                
            info_hash = extract_share_id(magnet, provider_hint="magnet")
            quality_tags = parse_quality_tags(title)
            
            size_str = _format_bytes(size_bytes) if size_bytes else ""

            results.append(
                SearchResult(
                    channel="torrent", normalized_channel="torrent",
                    source=self.name, upstream_source=indexer_name, provider="magnet",
                    title=title, link_or_magnet=_clean_magnet(magnet),
                    share_id_or_info_hash=info_hash,
                    size=size_str,
                    seeders=seeders,
                    quality=quality_display_from_tags(quality_tags), quality_tags=quality_tags,
                    raw={"title": title, "seeders": seeders},
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
