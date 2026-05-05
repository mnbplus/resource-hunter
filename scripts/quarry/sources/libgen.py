"""Library Genesis (LibGen) book/ebook source adapter (HTML scraper).

Scrapes LibGen search pages to find books, textbooks, and academic papers.
**No API key or login required** — pure HTML table parsing with multi-mirror fallback.

Search flow:
  1. ``GET /search.php?req={query}&lg_topic=libgen&open=0&view=simple&res=25&phrase=1&column=def``
  2. Parse HTML table rows → extract title, author, format, size, MD5
  3. Build download link from MD5 via ``library.lol``
  4. Map to ``SearchResult`` with ``provider="libgen"``

Mirror chain: libgen.rs → libgen.is → libgen.st
"""
from __future__ import annotations

import re
import urllib.parse
from typing import Any

from .base import HTTPClient, SourceAdapter, _format_size
from ..common import (
    compact_spaces,
    normalize_title,
    parse_quality_tags,
    quality_display_from_tags,
)
from ..exceptions import SourceNetworkError
from ..models import SearchIntent, SearchResult

_SEARCH_MIRRORS = (
    "libgen.rs",
    "libgen.is",
    "libgen.st",
)

_DOWNLOAD_BASE = "https://library.lol/main"

# Regex to extract table rows from the search results page
# LibGen uses a simple HTML table with <tr> rows
_TR_RE = re.compile(r"<tr\s[^>]*>(.*?)</tr>", re.DOTALL | re.I)
_TD_RE = re.compile(r"<td[^>]*>(.*?)</td>", re.DOTALL | re.I)
_LINK_RE = re.compile(r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', re.DOTALL | re.I)
_MD5_RE = re.compile(r"[0-9a-fA-F]{32}")


def _strip_html(text: str) -> str:
    """Remove HTML tags and normalize whitespace."""
    return compact_spaces(re.sub(r"<[^>]+>", " ", text))


def _parse_size_text(text: str) -> str:
    """Parse LibGen size format like '5 Mb' into '5.0MB'."""
    m = re.search(r"(\d+(?:\.\d+)?)\s*(Kb|Mb|Gb|Tb|KB|MB|GB|TB|kb|mb|gb|tb)", text)
    if not m:
        return ""
    val = float(m.group(1))
    unit = m.group(2).upper()
    # Normalize: LibGen uses "Mb" for megabytes (not megabits)
    if unit == "KB":
        return f"{val:.1f}KB"
    if unit == "MB":
        return f"{val:.1f}MB"
    if unit == "GB":
        return f"{val:.1f}GB"
    if unit == "TB":
        return f"{val:.1f}TB"
    return f"{val:.1f}{unit}"


class LibgenSource(SourceAdapter):
    """Library Genesis — massive open library of books, textbooks, and papers."""
    name = "libgen"
    channel = "torrent"
    priority = 2

    def search(
        self,
        query: str,
        intent: SearchIntent,
        limit: int,
        page: int,
        http_client: HTTPClient,
    ) -> list[SearchResult]:
        # Build search URL with appropriate parameters
        params = {
            "req": query,
            "lg_topic": "libgen",
            "open": "0",
            "view": "simple",
            "res": str(min(limit * 3, 50)),  # fetch more to filter
            "phrase": "1",
            "column": "def",
            "page": str(page),
        }
        search_path = f"/search.php?{urllib.parse.urlencode(params)}"

        # Try mirrors
        html = ""
        for mirror in _SEARCH_MIRRORS:
            url = f"https://{mirror}{search_path}"
            try:
                html = http_client.get_text(url, timeout=15)
                if html and len(html) > 1000:
                    break
            except Exception:
                continue

        if not html or len(html) < 500:
            raise SourceNetworkError("all libgen mirrors exhausted or empty response", source=self.name)

        return self._parse_results(html, limit)

    def _parse_results(self, html: str, limit: int) -> list[SearchResult]:
        """Parse LibGen search result HTML table."""
        results: list[SearchResult] = []

        # Find all table rows
        rows = _TR_RE.findall(html)
        if not rows:
            return []

        for row_html in rows:
            if len(results) >= limit:
                break

            # Skip header rows and non-data rows
            cells = _TD_RE.findall(row_html)
            if len(cells) < 8:
                continue

            result = self._parse_row(cells)
            if result:
                results.append(result)

        return results

    def _parse_row(self, cells: list[str]) -> SearchResult | None:
        """Parse a single table row into a SearchResult.

        LibGen simple view columns (typical):
        [0] ID  [1] Author(s)  [2] Title  [3] Publisher  [4] Year
        [5] Pages  [6] Language  [7] Size  [8] Extension  [9] Mirror links
        """
        if len(cells) < 9:
            return None

        # Extract author(s)
        author = _strip_html(cells[1])
        if len(author) > 100:
            author = author[:100] + "..."

        # Extract title — often contains links
        title_cell = cells[2]
        links = _LINK_RE.findall(title_cell)
        title = ""
        md5 = ""

        for href, link_text in links:
            clean_text = _strip_html(link_text)
            # The main title link usually points to /book/index.php or contains md5
            if clean_text and len(clean_text) > len(title):
                title = clean_text
            # Try to extract MD5 from href
            md5_match = _MD5_RE.search(href)
            if md5_match:
                md5 = md5_match.group(0).lower()

        if not title:
            title = _strip_html(title_cell)

        title = normalize_title(title)
        if not title or len(title) < 3:
            return None

        # Extract other fields
        publisher = _strip_html(cells[3]) if len(cells) > 3 else ""
        year = _strip_html(cells[4]).strip() if len(cells) > 4 else ""
        language = _strip_html(cells[6]).strip() if len(cells) > 6 else ""
        size_text = _strip_html(cells[7]).strip() if len(cells) > 7 else ""
        extension = _strip_html(cells[8]).strip().lower() if len(cells) > 8 else ""

        # Try to find MD5 from mirror links if not found in title
        if not md5 and len(cells) > 9:
            for cell in cells[9:]:
                md5_match = _MD5_RE.search(cell)
                if md5_match:
                    md5 = md5_match.group(0).lower()
                    break

        # If still no MD5, try all cells
        if not md5:
            for cell in cells:
                md5_match = _MD5_RE.search(cell)
                if md5_match:
                    md5 = md5_match.group(0).lower()
                    break

        if not md5:
            return None

        # Build display title
        display_title = title
        if author and author.lower() not in title.lower():
            display_title = f"{title} — {author}"
        if extension:
            display_title = f"{display_title} [{extension.upper()}]"

        # Parse size
        size = _parse_size_text(size_text)

        # Build download URL
        download_url = f"{_DOWNLOAD_BASE}/{md5}"

        # Quality tags
        quality_tags = parse_quality_tags(display_title)
        if extension:
            quality_tags["format"] = extension
            quality_tags["book_format"] = extension

        return SearchResult(
            channel="torrent",
            normalized_channel="torrent",
            source=self.name,
            upstream_source=self.name,
            provider="libgen",
            title=display_title,
            link_or_magnet=download_url,
            share_id_or_info_hash=md5,
            size=size,
            quality=quality_display_from_tags(quality_tags),
            quality_tags=quality_tags,
            raw={
                "author": author,
                "publisher": publisher,
                "year": year,
                "language": language,
                "format": extension,
                "md5": md5,
            },
        )
