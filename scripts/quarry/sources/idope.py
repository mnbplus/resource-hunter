"""iDope — DHT torrent search engine via HTML scraping (zero-config).

iDope is a DHT-based torrent search engine that indexes millions of torrents
from the distributed hash table network.  No login or API key required.

Search flow:
  1. GET ``https://idope.se/search/{query}/{page}/`` with sort parameters
  2. Parse search result divs → extract title, info_hash, size, seeders
  3. Construct magnet URI from info_hash
  4. Map to ``SearchResult`` with ``provider="magnet"``
"""
from __future__ import annotations

import re
import urllib.parse
from typing import Any

from .base import HTTPClient, SourceAdapter, _format_size, _make_magnet
from ..common import normalize_title, parse_quality_tags, quality_display_from_tags
from ..exceptions import SourceNetworkError, SourceParseError
from ..models import SearchIntent, SearchResult

_BASE_URL = "https://idope.se"

# Result block: each result is in a div with class "result"
_RESULT_RE = re.compile(
    r'<div\s[^>]*class=["\'][^"\']*result[^"\']*["\'][^>]*>(.*?)</div>\s*</div>',
    re.DOTALL | re.I,
)

# Title extraction from <a> tag with class "result_title"
_TITLE_RE = re.compile(
    r'<div[^>]*class=["\'][^"\']*div2[^"\']*["\'][^>]*>.*?'
    r'<a[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>',
    re.DOTALL | re.I,
)

# Info hash — iDope stores it in a hidden element or data attribute
_HASH_RE = re.compile(r'(?:hash|infohash|info_hash)["\s:=]+([0-9a-fA-F]{40})', re.I)

# Magnet link direct
_MAGNET_RE = re.compile(r'href="(magnet:\?[^"]+)"', re.I)

# Size extraction
_SIZE_RE = re.compile(r'<div[^>]*class=["\'][^"\']*resultdivbotton[^"\']*["\'][^>]*>(.*?)</div>', re.DOTALL | re.I)
_SIZE_VALUE_RE = re.compile(r"(\d+(?:\.\d+)?\s*(?:KB|MB|GB|TB|KiB|MiB|GiB|TiB|bytes?))", re.I)

# Seeders
_SEEDERS_RE = re.compile(r'<div[^>]*class=["\'][^"\']*resultdivbottonseed[^"\']*["\'][^>]*>(\d+)</div>', re.I)

# Alternative: broader extraction patterns
_ALT_TITLE_RE = re.compile(r'<a[^>]+href="/torrent/[^"]*"[^>]*>([^<]+)</a>', re.I)
_ALT_HASH_RE = re.compile(r'/torrent/([0-9a-fA-F]{40})/', re.I)
_ALT_SIZE_RE = re.compile(r'(\d+(?:\.\d+)?\s*(?:KB|MB|GB|TB))', re.I)
_ALT_SEEDS_RE = re.compile(r'(?:seed|seeder)s?\s*[:=]?\s*(\d+)', re.I)


def _strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", " ", text).strip()


class IDOPESource(SourceAdapter):
    """iDope — DHT network torrent search engine."""
    name = "idope"
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
        # iDope URL format: /search/{query}/{page}/
        encoded_query = urllib.parse.quote(query, safe="")
        url = f"{_BASE_URL}/search/{encoded_query}/{page}/"

        try:
            html = http_client.get_text(url, timeout=10)
        except Exception as exc:
            raise SourceNetworkError(str(exc), source=self.name, url=url) from exc

        if not html or len(html) < 500:
            raise SourceParseError("response too short or empty", source=self.name, url=url)

        return self._parse_results(html, limit)

    def _parse_results(self, html: str, limit: int) -> list[SearchResult]:
        results: list[SearchResult] = []

        # Strategy 1: Try structured block extraction
        blocks = _RESULT_RE.findall(html)
        if blocks:
            for block in blocks:
                if len(results) >= limit:
                    break
                result = self._parse_block(block)
                if result:
                    results.append(result)

        # Strategy 2: Fallback to link-based extraction
        if not results:
            results = self._parse_fallback(html, limit)

        return results[:limit]

    def _parse_block(self, block_html: str) -> SearchResult | None:
        """Parse a single result block."""
        # Extract title
        title_match = _TITLE_RE.search(block_html)
        if title_match:
            title = normalize_title(_strip_html(title_match.group(2)))
            detail_path = title_match.group(1)
        else:
            return None

        if not title or len(title) < 3:
            return None

        # Extract info hash
        info_hash = ""
        hash_match = _HASH_RE.search(block_html)
        if hash_match:
            info_hash = hash_match.group(1).lower()
        else:
            # Try from detail URL path
            alt_hash = _ALT_HASH_RE.search(block_html)
            if alt_hash:
                info_hash = alt_hash.group(1).lower()

        # Extract magnet
        magnet = ""
        magnet_match = _MAGNET_RE.search(block_html)
        if magnet_match:
            magnet = magnet_match.group(1)
            if not info_hash:
                btih_match = re.search(r"btih:([0-9a-fA-F]{40})", magnet, re.I)
                if btih_match:
                    info_hash = btih_match.group(1).lower()

        if not info_hash:
            return None

        if not magnet:
            magnet = _make_magnet(info_hash, title)

        # Extract size
        size = ""
        size_block = _SIZE_RE.search(block_html)
        if size_block:
            size_match = _SIZE_VALUE_RE.search(size_block.group(1))
            if size_match:
                size = size_match.group(1)
        if not size:
            alt_size = _ALT_SIZE_RE.search(block_html)
            if alt_size:
                size = alt_size.group(1)

        # Extract seeders
        seeders = 0
        seeds_match = _SEEDERS_RE.search(block_html)
        if seeds_match:
            seeders = int(seeds_match.group(1))
        else:
            alt_seeds = _ALT_SEEDS_RE.search(block_html)
            if alt_seeds:
                seeders = int(alt_seeds.group(1))

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

    def _parse_fallback(self, html: str, limit: int) -> list[SearchResult]:
        """Fallback: extract from <a> links pointing to /torrent/{hash}/."""
        results: list[SearchResult] = []
        seen: set[str] = set()

        for match in _ALT_TITLE_RE.finditer(html):
            if len(results) >= limit:
                break

            title = normalize_title(match.group(1))
            if not title or len(title) < 3:
                continue

            # Find info_hash near this title
            start = max(0, match.start() - 200)
            end = min(len(html), match.end() + 500)
            context = html[start:end]

            hash_match = _ALT_HASH_RE.search(context)
            if not hash_match:
                continue
            info_hash = hash_match.group(1).lower()

            if info_hash in seen:
                continue
            seen.add(info_hash)

            # Extract size and seeders from context
            size = ""
            size_match = _ALT_SIZE_RE.search(context)
            if size_match:
                size = size_match.group(1)

            seeders = 0
            seeds_match = _ALT_SEEDS_RE.search(context)
            if seeds_match:
                seeders = int(seeds_match.group(1))

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
                    size=size,
                    seeders=seeders,
                    quality=quality_display_from_tags(quality_tags),
                    quality_tags=quality_tags,
                    raw={},
                )
            )

        return results
