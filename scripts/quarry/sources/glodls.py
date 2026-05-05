"""GLODLS — general torrent index via HTML scraping (zero-config).

GLODLS (Global Downloads) is a public torrent indexer covering movies, TV,
software, music, and games.  No login or API key required.

Search flow:
  1. GET ``https://glodls.to/search_results.php?search={query}&cat=0&incldead=0&inclexternal=0&lang=0&sort=seeders&order=desc``
  2. Parse HTML table rows → extract title, magnet link, size, seeders
  3. Map to ``SearchResult`` with ``provider="magnet"``
"""
from __future__ import annotations

import re
import urllib.parse
from typing import Any

from .base import HTTPClient, SourceAdapter, _clean_magnet, _format_size, _make_magnet
from ..common import extract_share_id, normalize_title, parse_quality_tags, quality_display_from_tags
from ..exceptions import SourceNetworkError, SourceParseError
from ..models import SearchIntent, SearchResult

_BASE_URL = "https://glodls.to"

# Magnet link extraction
_MAGNET_RE = re.compile(r'href="(magnet:\?[^"]+)"', re.I)

# Table row extraction
_TR_RE = re.compile(r"<tr\s[^>]*class=['\"]t-row[^'\"]*['\"][^>]*>(.*?)</tr>", re.DOTALL | re.I)
_TD_RE = re.compile(r"<td[^>]*>(.*?)</td>", re.DOTALL | re.I)

# Title link — usually in the first/second td with an <a> tag
_TITLE_LINK_RE = re.compile(
    r'<a[^>]+href=["\']/(torrent/[^"\']+)["\'][^>]*title=["\']([^"\']+)["\']',
    re.I,
)

# Size and seeder patterns from table cells
_SIZE_RE = re.compile(r"(\d+(?:\.\d+)?\s*(?:KB|MB|GB|TB|KiB|MiB|GiB|TiB))", re.I)
_NUMBER_RE = re.compile(r"(\d+)")

# Info hash from magnet URI
_HASH_RE = re.compile(r"btih:([0-9a-fA-F]{40})", re.I)


def _strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", " ", text).strip()


class GLODLSSource(SourceAdapter):
    """GLODLS — Global Downloads public torrent index."""
    name = "glodls"
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
            "search": query,
            "cat": "0",       # all categories
            "incldead": "0",  # exclude dead torrents
            "inclexternal": "0",
            "lang": "0",      # all languages
            "sort": "seeders",
            "order": "desc",
        })
        url = f"{_BASE_URL}/search_results.php?{params}"

        try:
            html = http_client.get_text(url, timeout=12)
        except Exception as exc:
            raise SourceNetworkError(str(exc), source=self.name, url=url) from exc

        if not html or not html.strip():
            raise SourceParseError("empty response body", source=self.name, url=url)

        return self._parse_results(html, limit)

    def _parse_results(self, html: str, limit: int) -> list[SearchResult]:
        results: list[SearchResult] = []

        rows = _TR_RE.findall(html)
        for row_html in rows:
            if len(results) >= limit:
                break

            result = self._parse_row(row_html)
            if result:
                results.append(result)

        return results

    def _parse_row(self, row_html: str) -> SearchResult | None:
        """Parse a single GLODLS table row."""
        # Extract title from link
        title_match = _TITLE_LINK_RE.search(row_html)
        if not title_match:
            return None
        title = normalize_title(_strip_html(title_match.group(2)))
        if not title or len(title) < 3:
            return None

        # Extract magnet link
        magnet_match = _MAGNET_RE.search(row_html)
        if not magnet_match:
            return None
        magnet = _clean_magnet(magnet_match.group(1))

        # Extract info hash
        hash_match = _HASH_RE.search(magnet)
        info_hash = hash_match.group(1).lower() if hash_match else ""

        # Extract cells for size/seeders
        cells = _TD_RE.findall(row_html)

        # Parse size and seeders from cells
        size = ""
        seeders = 0
        for cell in cells:
            cell_text = _strip_html(cell)
            if not size:
                size_match = _SIZE_RE.search(cell_text)
                if size_match:
                    size = size_match.group(1)

        # Seeders/leechers are usually in td elements with class containing "seeds" or a green color
        seeds_matches = re.findall(
            r'<td[^>]*class=["\'][^"\']*(?:seeds|green)[^"\']*["\'][^>]*>(\d+)</td>',
            row_html, re.I,
        )
        if seeds_matches:
            seeders = int(seeds_matches[0])
        else:
            # Fallback: look for plain numbers in the last few cells
            if len(cells) >= 4:
                for cell in cells[-3:]:
                    num_text = _strip_html(cell)
                    if num_text.isdigit() and int(num_text) < 100000:
                        seeders = int(num_text)
                        break

        quality_tags = parse_quality_tags(title)

        return SearchResult(
            channel="torrent",
            normalized_channel="torrent",
            source=self.name,
            upstream_source=self.name,
            provider="magnet",
            title=title,
            link_or_magnet=magnet,
            share_id_or_info_hash=info_hash,
            size=size,
            seeders=seeders,
            quality=quality_display_from_tags(quality_tags),
            quality_tags=quality_tags,
            raw={},
        )
