"""BTDigg DHT search adapter (HTML scraper, zero-config).

BTDigg indexes the BitTorrent DHT network directly, providing magnet links
for content that may not appear on traditional trackers.  Excellent for
long-tail and niche content.

No registration, no API key, no login required.
"""
from __future__ import annotations

import html as html_mod
import re
import urllib.parse

from .base import HTTPClient, SourceAdapter, _clean_magnet
from ..common import extract_share_id, normalize_title, parse_quality_tags, quality_display_from_tags
from ..exceptions import SourceNetworkError
from ..models import SearchIntent, SearchResult

# BTDigg search results are rendered in a simple list format
# Each result has: title link, magnet link, size, date, files count
_RESULT_BLOCK_RE = re.compile(
    r'<div class="one_result">(.*?)</div>\s*(?=<div class="one_result"|<div class="pag")',
    re.I | re.S,
)
_TITLE_RE = re.compile(r'<div class="torrent_name">\s*<a[^>]*>([^<]+)</a>', re.I | re.S)
_MAGNET_RE = re.compile(r'href="(magnet:\?[^"]+)"', re.I)
_SIZE_RE = re.compile(r'<span class="torrent_size"[^>]*>\s*([^<]+)', re.I)
_DATE_RE = re.compile(r'<span class="torrent_age"[^>]*>\s*([^<]+)', re.I)
_FILES_RE = re.compile(r'<span class="torrent_files"[^>]*>\s*([^<]+)', re.I)

# Fallback patterns for alternative BTDigg layouts
_ALT_TITLE_RE = re.compile(r'<td class="torrent_name">\s*<a[^>]*>([^<]+)</a>', re.I | re.S)
_ALT_BLOCK_RE = re.compile(r'<tr class="torrent_row">(.*?)</tr>', re.I | re.S)


class BTDiggSource(SourceAdapter):
    name = "btdig"
    channel = "torrent"
    priority = 3  # backup DHT source

    MIRRORS = (
        "btdig.com",
        "btdig.net",
    )

    def search(self, query: str, intent: SearchIntent, limit: int, page: int, http_client: HTTPClient) -> list[SearchResult]:
        import urllib.parse as _urlparse
        path = f"/search?q={_urlparse.quote(query)}&p={page - 1}&order=0"
        try:
            payload = http_client.get_text_with_mirrors(self.name, self.MIRRORS, path, timeout=10)
        except Exception as exc:
            raise SourceNetworkError(str(exc), source=self.name, url=path) from exc

        results: list[SearchResult] = []

        # Try primary layout first
        blocks = _RESULT_BLOCK_RE.findall(payload)

        # Fallback to alternative table layout
        if not blocks:
            blocks = _ALT_BLOCK_RE.findall(payload)

        if not blocks:
            # Last resort: extract magnets directly from the page
            return self._extract_magnets_fallback(payload, limit)

        for block in blocks:
            title_match = _TITLE_RE.search(block) or _ALT_TITLE_RE.search(block)
            if not title_match:
                continue

            title = normalize_title(html_mod.unescape(title_match.group(1).strip()))
            if not title:
                continue

            magnet_match = _MAGNET_RE.search(block)
            if not magnet_match:
                continue

            raw_magnet = html_mod.unescape(magnet_match.group(1))
            info_hash = extract_share_id(raw_magnet, provider_hint="magnet")
            if not info_hash:
                continue

            size_match = _SIZE_RE.search(block)
            size_str = size_match.group(1).strip() if size_match else ""

            date_match = _DATE_RE.search(block)
            date_str = date_match.group(1).strip() if date_match else ""

            quality_tags = parse_quality_tags(title)

            results.append(
                SearchResult(
                    channel="torrent", normalized_channel="torrent",
                    source=self.name, upstream_source=self.name, provider="magnet",
                    title=title, link_or_magnet=_clean_magnet(raw_magnet),
                    share_id_or_info_hash=info_hash,
                    size=size_str, seeders=0,  # DHT doesn't provide seeder counts
                    quality=quality_display_from_tags(quality_tags), quality_tags=quality_tags,
                    raw={"title": title, "date": date_str, "dht": True},
                )
            )

            if len(results) >= limit:
                break

        return results

    def _extract_magnets_fallback(self, payload: str, limit: int) -> list[SearchResult]:
        """Last-resort extraction: find all magnet links with nearby text."""
        results: list[SearchResult] = []
        for match in _MAGNET_RE.finditer(payload):
            raw_magnet = html_mod.unescape(match.group(1))
            info_hash = extract_share_id(raw_magnet, provider_hint="magnet")
            if not info_hash:
                continue

            # Try to extract dn= from magnet as title
            dn_match = re.search(r'dn=([^&]+)', raw_magnet)
            title = normalize_title(urllib.parse.unquote_plus(dn_match.group(1))) if dn_match else info_hash

            quality_tags = parse_quality_tags(title)
            results.append(
                SearchResult(
                    channel="torrent", normalized_channel="torrent",
                    source=self.name, upstream_source=self.name, provider="magnet",
                    title=title, link_or_magnet=_clean_magnet(raw_magnet),
                    share_id_or_info_hash=info_hash,
                    size="", seeders=0,
                    quality=quality_display_from_tags(quality_tags), quality_tags=quality_tags,
                    raw={"title": title, "dht": True, "fallback": True},
                )
            )
            if len(results) >= limit:
                break
        return results
