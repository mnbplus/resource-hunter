"""TorrentGalaxy torrent source adapter with mirror failover.

One of the top general-purpose public trackers (movies, TV, games,
software, music). Considered the best RARBG replacement. Community-
verified uploads, rich metadata. May require FlareSolverr behind
Cloudflare on some mirrors.
"""
from __future__ import annotations
import html as html_mod
import re
import urllib.parse
from .base import HTTPClient, SourceAdapter, _clean_magnet
from ..common import extract_share_id, normalize_title, parse_quality_tags, quality_display_from_tags
from ..models import SearchIntent, SearchResult

# TorrentGalaxy uses div-based table layout with class "tgxtablerow"
_ROW_SPLIT_RE = re.compile(r'<div class="tgxtablerow[^"]*"')
# Title: extracted from <b>TITLE</b> inside the clickable row div
# The post-detail link contains <span><b>Title.Here</b></span>
_TITLE_BOLD_RE = re.compile(
    r'href="/post-detail/[^"]*"[^>]*>.*?<b>([^<]+)</b>',
    re.I | re.S,
)
# Fallback: any <b> tag with reasonable content length
_TITLE_ANY_BOLD_RE = re.compile(r'<b>([^<]{10,})</b>', re.I)
_MAGNET_RE = re.compile(r'href="(magnet:\?[^"]+)"', re.I)
_SEEDERS_RE = re.compile(
    r'<font color="green">\s*(?:<b>)?\s*(\d+)\s*(?:</b>)?\s*</font>',
    re.I | re.S,
)
_SIZE_RE = re.compile(
    r'<span class="badge badge-secondary[^"]*">(\d+[\.\d]*\s*[KMGTP]?i?B)</span>',
    re.I,
)


class TorrentGalaxySource(SourceAdapter):
    name = "torrentgalaxy"
    channel = "torrent"
    priority = 2

    MIRRORS = (
        "torrentgalaxy.one",
        "torrentgalaxy.to",
        "torrentgalaxy.hair",
        "torrentgalaxy.info",
    )

    _KIND_TO_CATEGORY = {
        "movie": "c3=1&c46=1&c45=1&c42=1&c4=1&c1=1",
        "tv": "c41=1&c5=1&c11=1&c6=1&c7=1",
        "anime": "c28=1",
        "music": "c22=1&c26=1&c23=1&c25=1&c24=1",
        "software": "c20=1&c21=1&c18=1",
        "book": "c13=1&c19=1",
    }

    def search(self, query: str, intent: SearchIntent, limit: int, page: int, http_client: HTTPClient) -> list[SearchResult]:
        cat_params = self._KIND_TO_CATEGORY.get(intent.kind, "")
        cat_suffix = f"&{cat_params}" if cat_params else ""
        path = f"/torrents.php?search={urllib.parse.quote(query)}&sort=seeders&order=desc&page={page - 1}{cat_suffix}"
        payload = http_client.get_text_with_mirrors(self.name, self.MIRRORS, path)

        results: list[SearchResult] = []
        blocks = _ROW_SPLIT_RE.split(payload)

        for block in blocks[1:]:
            # Must have a magnet link to be useful
            magnet_match = _MAGNET_RE.search(block)
            if not magnet_match:
                continue

            # Extract title from <b> inside the post-detail link
            title_match = _TITLE_BOLD_RE.search(block)
            if not title_match:
                # Fallback: any reasonable <b> tag
                title_match = _TITLE_ANY_BOLD_RE.search(block)
            if not title_match:
                continue

            title = normalize_title(html_mod.unescape(title_match.group(1)))
            if not title:
                continue

            raw_magnet = html_mod.unescape(magnet_match.group(1))
            info_hash = extract_share_id(raw_magnet, provider_hint="magnet")
            if not info_hash:
                continue

            size_match = _SIZE_RE.search(block)
            size_str = size_match.group(1).strip() if size_match else ""

            seeders_match = _SEEDERS_RE.search(block)
            seeders = int(seeders_match.group(1)) if seeders_match else 0

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

            if len(results) >= limit:
                break

        return results
