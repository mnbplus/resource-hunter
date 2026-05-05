"""Anna's Archive book/ebook source adapter (HTML scraper).

Scrapes ``annas-archive.org/search`` to find books and ebooks.
**No API key or login required** for search.

Search flow:
  1. ``GET /search?q={query}&ext={ext}&lang={lang}`` → parse result listing
  2. Extract: title, author, format, size, language, detail page link
  3. Map to ``SearchResult`` with ``provider="annas_archive"``

Protected by DDoS-Guard — uses ``curl_cffi`` if available for TLS
fingerprint impersonation, otherwise falls back to standard urllib.
"""
from __future__ import annotations

import re
import urllib.parse
from typing import Any

from .base import HTTPClient, SourceAdapter
from ..common import (
    compact_spaces,
    normalize_title,
    parse_quality_tags,
    quality_display_from_tags,
)
from ..exceptions import SourceNetworkError
from ..models import SearchIntent, SearchResult

_MIRRORS = [
    "annas-archive.org",
    "annas-archive.se",
    "annas-archive.li",
    "annas-archive.gs",
]


# Each result is a large <a> block linking to /md5/{hash}
# Pattern: <a href="/md5/..." class="...">
_RESULT_BLOCK_RE = re.compile(
    r'<a\s+href="(/md5/[0-9a-fA-F]+)"[^>]*class="[^"]*js-vim-focus[^"]*"[^>]*>(.*?)</a>',
    re.DOTALL,
)

# Fallback: simpler link pattern
_MD5_LINK_RE = re.compile(
    r'href="(/md5/([0-9a-fA-F]{32}))"',
)

# Title: usually in a <h3> or prominent text node
_TITLE_RE = re.compile(r'<h3[^>]*>(.*?)</h3>', re.DOTALL)

# Author: often in italic or specific class
_AUTHOR_RE = re.compile(r'<div[^>]*class="[^"]*italic[^"]*"[^>]*>([^<]+)</div>')

# File info: format, size, language
_FILEINFO_RE = re.compile(
    r'\b(pdf|epub|mobi|azw3|djvu|cbr|cbz|fb2|txt)\b',
    re.I,
)
_SIZE_RE = re.compile(r'(\d+(?:\.\d+)?\s*[KMGT]?B)\b', re.I)
_LANG_RE = re.compile(r'\b(English|Chinese|Japanese|Korean|French|German|Spanish|Russian|Portuguese)\b', re.I)


def _strip_html(text: str) -> str:
    """Remove HTML tags."""
    return re.sub(r'<[^>]+>', ' ', text).strip()


def _clean_text(text: str) -> str:
    """Strip HTML and normalize whitespace."""
    return compact_spaces(_strip_html(text))


def _build_search_url(
    base: str,
    query: str,
    ext: str = "",
    lang: str = "",
    content: str = "",
) -> str:
    """Build Anna's Archive search URL with filters."""
    params: dict[str, str] = {"q": query}
    if ext:
        params["ext"] = ext
    if lang:
        params["lang"] = lang
    if content:
        params["content"] = content
    return f"{base}/search?{urllib.parse.urlencode(params)}"


class AnnasArchiveSource(SourceAdapter):
    """Anna's Archive — the world's largest open-source library search engine."""
    name = "annas"
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
        # Determine extension filter from intent
        ext = ""
        if intent.format_hints:
            # Use the first format hint (e.g., "pdf", "epub")
            for hint in intent.format_hints:
                if hint.lower() in {"pdf", "epub", "mobi", "azw3", "djvu", "txt", "cbr", "cbz"}:
                    ext = hint.lower()
                    break

        # Determine content type
        content = ""
        query_lower = (intent.original_query or query).lower()
        fiction_hints = ("小说", "novel", "fiction", "light novel", "轻小说", "网文")
        if any(h in query_lower for h in fiction_hints):
            content = "book_fiction"

        for base in [f"https://{m}" for m in _MIRRORS]:
            url = _build_search_url(base, query, ext=ext, content=content)
            try:
                html = http_client.get_text(url, timeout=15)
                if html and len(html) > 500:
                    return self._parse_results(html, limit)
            except Exception:
                continue

        raise SourceNetworkError("all mirrors exhausted", source=self.name)

    def _parse_results(self, html: str, limit: int) -> list[SearchResult]:
        """Parse search result page HTML."""
        results: list[SearchResult] = []

        # Strategy 1: Try structured block extraction
        for block_match in _RESULT_BLOCK_RE.finditer(html):
            if len(results) >= limit:
                break
            detail_path = block_match.group(1)
            block_html = block_match.group(2)
            result = self._parse_single_block(detail_path, block_html)
            if result:
                results.append(result)

        # Strategy 2: If no structured blocks found, use simpler link extraction
        if not results:
            seen_md5: set[str] = set()
            for link_match in _MD5_LINK_RE.finditer(html):
                if len(results) >= limit:
                    break
                detail_path = link_match.group(1)
                md5 = link_match.group(2).lower()
                if md5 in seen_md5:
                    continue
                seen_md5.add(md5)

                # Extract context around the link
                start = max(0, link_match.start() - 50)
                end = min(len(html), link_match.end() + 800)
                context = html[start:end]
                result = self._parse_context_block(detail_path, md5, context)
                if result:
                    results.append(result)

        return results

    def _parse_single_block(self, detail_path: str, block_html: str) -> SearchResult | None:
        """Parse a single result block."""
        # Extract title
        title_match = _TITLE_RE.search(block_html)
        title = _clean_text(title_match.group(1)) if title_match else ""
        if not title:
            # Fallback: first substantial text
            text = _clean_text(block_html)
            title = text[:120] if text else ""
        if not title or len(title) < 3:
            return None

        title = normalize_title(title)

        # Extract author
        author_match = _AUTHOR_RE.search(block_html)
        author = _clean_text(author_match.group(1)) if author_match else ""

        # Extract file info
        block_text = _clean_text(block_html)
        fmt_match = _FILEINFO_RE.search(block_text)
        file_format = fmt_match.group(1).lower() if fmt_match else ""

        size_match = _SIZE_RE.search(block_text)
        size = size_match.group(1) if size_match else ""

        lang_match = _LANG_RE.search(block_text)
        language = lang_match.group(1) if lang_match else ""

        # Build full title with metadata
        display_title = title
        if author:
            display_title = f"{title} — {author}"
        if file_format:
            display_title = f"{display_title} [{file_format.upper()}]"

        detail_url = f"https://{_MIRRORS[0]}{detail_path}"
        md5 = detail_path.split("/")[-1].lower()

        quality_tags = parse_quality_tags(display_title)
        if file_format:
            quality_tags["format"] = file_format
            quality_tags["book_format"] = file_format

        return SearchResult(
            channel="torrent",
            normalized_channel="torrent",
            source=self.name,
            upstream_source=self.name,
            provider="annas_archive",
            title=display_title,
            link_or_magnet=detail_url,
            share_id_or_info_hash=md5,
            size=size,
            quality=quality_display_from_tags(quality_tags),
            quality_tags=quality_tags,
            raw={
                "author": author,
                "format": file_format,
                "language": language,
                "detail_url": detail_url,
            },
        )

    def _parse_context_block(
        self, detail_path: str, md5: str, context: str
    ) -> SearchResult | None:
        """Parse a result from surrounding context of an MD5 link."""
        text = _clean_text(context)
        if not text or len(text) < 5:
            return None

        # First line-ish chunk is usually the title
        lines = [line.strip() for line in text.split("\n") if line.strip()]
        title = normalize_title(lines[0][:150]) if lines else ""
        if not title:
            return None

        full_text = " ".join(lines)
        fmt_match = _FILEINFO_RE.search(full_text)
        file_format = fmt_match.group(1).lower() if fmt_match else ""

        size_match = _SIZE_RE.search(full_text)
        size = size_match.group(1) if size_match else ""

        display_title = title
        if file_format:
            display_title = f"{title} [{file_format.upper()}]"

        quality_tags = parse_quality_tags(display_title)
        if file_format:
            quality_tags["format"] = file_format
            quality_tags["book_format"] = file_format

        return SearchResult(
            channel="torrent",
            normalized_channel="torrent",
            source=self.name,
            upstream_source=self.name,
            provider="annas_archive",
            title=display_title,
            link_or_magnet=f"https://{_MIRRORS[0]}{detail_path}",
            share_id_or_info_hash=md5,
            size=size,
            quality=quality_display_from_tags(quality_tags),
            quality_tags=quality_tags,
            raw={
                "format": file_format,
                "detail_url": f"https://{_MIRRORS[0]}{detail_path}",
            },
        )
