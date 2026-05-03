"""TorLock torrent source adapter (verified torrents).

TorLock focuses on verified torrents with strict quality control.
No fake torrents — every listing is verified. Good for safe, clean
resource discovery across movies, TV, anime, music, software, and books.

Note: TorLock search results only show titles and metadata (seeders, size).
Magnet links are obtained from external relay pages. We construct
.torrent URLs as a fallback when magnet links are unavailable.
"""
from __future__ import annotations
import html as html_mod
import re
import urllib.parse
from .base import HTTPClient, SourceAdapter, TRACKERS, _clean_magnet, _make_magnet
from ..common import extract_share_id, normalize_title, parse_quality_tags, quality_display_from_tags
from ..models import SearchIntent, SearchResult

# TorLock now uses external relay links like: http://xxx.t0r.space/torrent/{id}/...
_RELAY_LINK_RE = re.compile(
    r'href="(?:https?://[^"]*t0r\.space)?/torrent/(\d+)/[^"]*"[^>]*>\s*(?:<b>)?(.*?)(?:</b>)?\s*</a>',
    re.I | re.S,
)
# Seeders/leechers in table cells
_CELLS_RE = re.compile(r'<td[^>]*>(.*?)</td>', re.I | re.S)
_NUMBER_RE = re.compile(r'^\s*(\d[\d,]*)\s*$')


class TorLockSource(SourceAdapter):
    name = "torlock"
    channel = "torrent"
    priority = 3

    BASE_URL = "https://www.torlock.com"

    _KIND_TO_PATH = {
        "movie": "movies",
        "tv": "television",
        "anime": "anime",
        "music": "music",
        "software": "software",
        "book": "ebooks",
    }

    def search(self, query: str, intent: SearchIntent, limit: int, page: int, http_client: HTTPClient) -> list[SearchResult]:
        cat_path = self._KIND_TO_PATH.get(intent.kind, "all")
        url = f"{self.BASE_URL}/{cat_path}/torrents/{urllib.parse.quote(query)}.html?sort=seeds&page={page}"
        payload = http_client.get_text(url)

        results: list[SearchResult] = []

        # Split into table rows
        rows = re.split(r'<tr[^>]*>', payload)
        for row in rows:
            # Find torrent links (either relay or direct)
            link_match = _RELAY_LINK_RE.search(row)
            if not link_match:
                continue

            torrent_id = link_match.group(1)
            raw_title = link_match.group(2).strip()
            # Clean HTML tags from title
            title = normalize_title(html_mod.unescape(re.sub(r'<[^>]+>', '', raw_title)))
            if not title or len(title) < 5:
                continue

            # Extract size and seeders from table cells
            cells = _CELLS_RE.findall(row)
            size_str = ""
            seeders = 0

            # Parse numeric cells for seeders/leechers/size
            # TorLock table: [title, date, size, seeders, leechers]
            numeric_cells: list[str] = []
            size_candidates: list[str] = []
            for cell in cells:
                clean = re.sub(r'<[^>]+>', '', cell).strip()
                if _NUMBER_RE.match(clean):
                    numeric_cells.append(clean.replace(",", ""))
                elif re.match(r'\d+[\.\d]*\s*[KMGTP]?i?B', clean, re.I):
                    size_candidates.append(clean)

            if size_candidates:
                size_str = size_candidates[0]
            if len(numeric_cells) >= 2:
                # Last two numeric cells are typically seeders, leechers
                seeders = int(numeric_cells[-2])
            elif len(numeric_cells) == 1:
                seeders = int(numeric_cells[0])

            # Build a .torrent download URL (magnet requires detail page)
            torrent_url = f"{self.BASE_URL}/tor/{torrent_id}.torrent"

            quality_tags = parse_quality_tags(title)

            results.append(
                SearchResult(
                    channel="torrent", normalized_channel="torrent",
                    source=self.name, upstream_source=self.name,
                    provider="torrent",
                    title=title, link_or_magnet=torrent_url,
                    share_id_or_info_hash="",
                    size=size_str, seeders=seeders,
                    quality=quality_display_from_tags(quality_tags), quality_tags=quality_tags,
                    raw={"title": title, "seeders": seeders, "torrent_id": torrent_id},
                )
            )

            if len(results) >= limit:
                break

        return results
