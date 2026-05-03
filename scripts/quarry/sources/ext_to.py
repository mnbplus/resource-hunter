"""EXT.to modern magnet search engine adapter.

A newer-generation torrent/magnet search engine with clean interface,
direct magnet links, and fast indexing. Growing community presence
since 2025. Covers movies, TV, anime, music, software, books.
"""
from __future__ import annotations
import html as html_mod
import re
import urllib.parse
from .base import HTTPClient, SourceAdapter, _clean_magnet
from ..common import extract_share_id, normalize_title, parse_quality_tags, quality_display_from_tags
from ..models import SearchIntent, SearchResult

_MAGNET_RE = re.compile(r'href="(magnet:\?[^"]+)"', re.I)
_TITLE_RE = re.compile(r'<a[^>]+href="/torrent/[^"]*"[^>]*title="([^"]*)"', re.I)
_SIZE_RE = re.compile(r'<td[^>]*class="[^"]*size[^"]*"[^>]*>([^<]+)</td>', re.I)
_SEEDERS_RE = re.compile(r'<td[^>]*class="[^"]*seeds?[^"]*"[^>]*>\s*(\d+)\s*</td>', re.I)
_ROW_SPLIT_RE = re.compile(r'<tr[^>]*class="[^"]*result[^"]*"', re.I)


class ExtToSource(SourceAdapter):
    name = "ext_to"
    channel = "torrent"
    priority = 3

    BASE_URL = "https://ext.to"

    _KIND_TO_CATEGORY = {
        "movie": "movies",
        "tv": "tv",
        "anime": "anime",
        "music": "music",
        "software": "software",
        "book": "books",
    }

    def search(self, query: str, intent: SearchIntent, limit: int, page: int, http_client: HTTPClient) -> list[SearchResult]:
        cat = self._KIND_TO_CATEGORY.get(intent.kind, "")
        cat_param = f"&c={cat}" if cat else ""
        url = f"{self.BASE_URL}/search/?q={urllib.parse.quote(query)}&sort=seeders&order=desc&p={page}{cat_param}"

        try:
            payload = http_client.get_text(url)
        except RuntimeError as exc:
            if "403" in str(exc):
                # Cloudflare protection — can't bypass without FlareSolverr
                return []
            raise
        results: list[SearchResult] = []

        # Strategy 1: Try block-based splitting
        blocks = _ROW_SPLIT_RE.split(payload)
        if len(blocks) > 1:
            for block in blocks[1:]:
                result = self._parse_block(block)
                if result:
                    results.append(result)
                    if len(results) >= limit:
                        break
            return results

        # Strategy 2: Fallback — extract all magnets + titles as parallel lists
        titles = _TITLE_RE.findall(payload)
        magnets = _MAGNET_RE.findall(payload)
        sizes = _SIZE_RE.findall(payload)
        seeders_list = _SEEDERS_RE.findall(payload)

        for i in range(min(len(titles), len(magnets), limit)):
            title = normalize_title(html_mod.unescape(titles[i]))
            if not title:
                continue
            raw_magnet = html_mod.unescape(magnets[i])
            info_hash = extract_share_id(raw_magnet, provider_hint="magnet")
            if not info_hash:
                continue

            size_str = html_mod.unescape(sizes[i]).strip() if i < len(sizes) else ""
            seeders = int(seeders_list[i]) if i < len(seeders_list) and seeders_list[i].isdigit() else 0

            quality_tags = parse_quality_tags(title)
            results.append(
                SearchResult(
                    channel="torrent", normalized_channel="torrent",
                    source=self.name, upstream_source=self.name, provider="magnet",
                    title=title, link_or_magnet=_clean_magnet(raw_magnet),
                    share_id_or_info_hash=info_hash,
                    size=size_str, seeders=seeders,
                    quality=quality_display_from_tags(quality_tags), quality_tags=quality_tags,
                    raw={"title": title, "seeders": seeders},
                )
            )

        return results

    @staticmethod
    def _parse_block(block: str) -> SearchResult | None:
        title_match = _TITLE_RE.search(block)
        if not title_match:
            return None
        title = normalize_title(html_mod.unescape(title_match.group(1)))
        if not title:
            return None

        magnet_match = _MAGNET_RE.search(block)
        if not magnet_match:
            return None
        raw_magnet = html_mod.unescape(magnet_match.group(1))
        info_hash = extract_share_id(raw_magnet, provider_hint="magnet")
        if not info_hash:
            return None

        size_match = _SIZE_RE.search(block)
        size_str = html_mod.unescape(size_match.group(1)).strip() if size_match else ""

        seeders_match = _SEEDERS_RE.search(block)
        seeders = int(seeders_match.group(1)) if seeders_match else 0

        quality_tags = parse_quality_tags(title)
        return SearchResult(
            channel="torrent", normalized_channel="torrent",
            source="ext_to", upstream_source="ext_to", provider="magnet",
            title=title, link_or_magnet=_clean_magnet(raw_magnet),
            share_id_or_info_hash=info_hash,
            size=size_str, seeders=seeders,
            quality=quality_display_from_tags(quality_tags), quality_tags=quality_tags,
            raw={"title": title, "seeders": seeders},
        )
