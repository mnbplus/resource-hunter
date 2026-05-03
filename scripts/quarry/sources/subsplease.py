"""SubsPlease (subsplease.org) anime torrent source adapter (RSS).

SubsPlease is a reliable anime fansub group that provides fast, high-quality
releases (720p/1080p/480p) of currently airing anime. Uses a clean RSS feed
with magnet links. Perfect for seasonal anime tracking.
"""
from __future__ import annotations
import re
import urllib.parse
from xml.etree import ElementTree
from .base import HTTPClient, SourceAdapter, TRACKERS, _clean_magnet, _format_size
from ..common import extract_share_id, normalize_title, parse_quality_tags, quality_display_from_tags
from ..models import SearchIntent, SearchResult

_MAGNET_HASH_RE = re.compile(r"btih:([0-9a-fA-F]{32,40})", re.I)
_SIZE_RE = re.compile(r"(\d+[\.\d]*\s*[KMGTP]?i?B)", re.I)


class SubsPleaseSource(SourceAdapter):
    name = "subsplease"
    channel = "torrent"
    priority = 1

    BASE_URL = "https://subsplease.org"

    # Resolution mapping for the RSS feed
    _RES_MAP = {
        "1080": "1080",
        "720": "720",
        "480": "480",
    }

    def search(self, query: str, intent: SearchIntent, limit: int, page: int, http_client: HTTPClient) -> list[SearchResult]:
        # SubsPlease RSS supports ?t=search_term&r=resolution
        # Resolution: 1080, 720, 480, or empty for all
        res = "1080"  # default to 1080p
        if intent and intent.original_query:
            lowered = intent.original_query.lower()
            if "720" in lowered:
                res = "720"
            elif "480" in lowered:
                res = "480"

        qs = urllib.parse.urlencode({"t": query, "r": res})
        url = f"{self.BASE_URL}/rss/?{qs}"
        payload = http_client.get_text(url)

        try:
            root = ElementTree.fromstring(payload)
        except ElementTree.ParseError:
            return []

        results: list[SearchResult] = []
        for item in root.findall("./channel/item")[: max(limit * 3, 12)]:
            title = normalize_title(item.findtext("title", ""))
            if not title:
                continue

            # SubsPlease RSS provides magnet link in the <link> tag
            link_text = item.findtext("link", "")
            magnet = ""
            info_hash = ""

            if link_text and link_text.startswith("magnet:"):
                magnet = link_text
                hash_match = _MAGNET_HASH_RE.search(magnet)
                if hash_match:
                    info_hash = hash_match.group(1).lower()
            else:
                continue  # no magnet = skip

            # Try to extract size from description
            size_str = ""
            desc = item.findtext("description", "")
            size_match = _SIZE_RE.search(desc)
            if size_match:
                size_str = size_match.group(1)

            # SubsPlease namespace extension for size
            ns_size = item.findtext("{https://subsplease.org/rss}size", "")
            if ns_size:
                size_str = ns_size

            quality_tags = parse_quality_tags(title)
            pub_date = item.findtext("pubDate", "")

            results.append(
                SearchResult(
                    channel="torrent", normalized_channel="torrent",
                    source=self.name, upstream_source=self.name, provider="magnet",
                    title=title, link_or_magnet=_clean_magnet(magnet),
                    share_id_or_info_hash=info_hash,
                    size=size_str,
                    seeders=0,
                    quality=quality_display_from_tags(quality_tags), quality_tags=quality_tags,
                    raw={"title": title, "pub_date": pub_date},
                )
            )
        return results
