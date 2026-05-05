"""1337x torrent source adapter with mirror failover and concurrent magnet fetching."""
from __future__ import annotations
import html
import re
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from .base import HTTPClient, SourceAdapter, _clean_magnet
from ..common import extract_share_id, normalize_title, parse_quality_tags, quality_display_from_tags
from ..models import SearchIntent, SearchResult


class OneThreeThreeSevenXSource(SourceAdapter):
    name = "1337x"
    channel = "torrent"
    priority = 3

    MIRRORS = ("www.1377x.to", "www.1337x.to", "1337x.st", "x1337x.ws")

    SEARCH_ROW_RE = re.compile(
        r'<a href="(?P<detail>/torrent/[^"]+)"[^>]*>(?P<title>[^<]+)</a>.*?'
        r'class="coll-4[^"]*">(?P<size>.*?)</td>.*?'
        r'class="coll-2[^"]*">(?P<seeds>\d+)</td>.*?'
        r'class="coll-3[^"]*">(?P<leeches>\d+)</td>',
        re.S,
    )

    def _fetch_magnet(self, detail_url: str, http_client: HTTPClient) -> str | None:
        try:
            detail_payload = http_client.get_text(detail_url)
            magnet_match = re.search(r'href="(magnet:[^"]+)"', detail_payload)
            return _clean_magnet(magnet_match.group(1)) if magnet_match else None
        except Exception:
            return None

    def search(self, query: str, intent: SearchIntent, limit: int, page: int, http_client: HTTPClient) -> list[SearchResult]:
        path = f"/search/{urllib.parse.quote(query)}/{page}/"
        payload = http_client.get_text_with_mirrors(self.name, self.MIRRORS, path)
        # Determine which mirror succeeded for detail page fetches
        from ..mirror_health import get_mirror_tracker
        tracker = get_mirror_tracker()
        ordered = tracker.ordered_mirrors(self.name, self.MIRRORS)
        base_domain = ordered[0] if ordered else self.MIRRORS[0]
        candidates: list[tuple[str, str, str, int]] = []
        for match in self.SEARCH_ROW_RE.finditer(payload):
            detail_path = html.unescape(match.group("detail"))
            detail_url = f"https://{base_domain}" + detail_path
            title = normalize_title(html.unescape(match.group("title")))
            size = normalize_title(html.unescape(match.group("size")))
            seeds = int(match.group("seeds"))
            candidates.append((detail_url, title, size, seeds))
            if len(candidates) >= max(limit * 2, 8):
                break

        if not candidates:
            return []

        magnets: dict[str, str | None] = {}
        with ThreadPoolExecutor(max_workers=min(4, len(candidates))) as executor:
            future_map = {
                executor.submit(self._fetch_magnet, detail_url, http_client): detail_url
                for detail_url, _, _, _ in candidates
            }
            for future in as_completed(future_map):
                detail_url = future_map[future]
                magnets[detail_url] = future.result()

        results: list[SearchResult] = []
        for detail_url, title, size, seeds in candidates:
            magnet = magnets.get(detail_url)
            if not magnet:
                continue
            info_hash = extract_share_id(magnet, provider_hint="magnet")
            quality_tags = parse_quality_tags(title)
            results.append(
                SearchResult(
                    channel="torrent", normalized_channel="torrent",
                    source=self.name, upstream_source=self.name, provider="magnet",
                    title=title, link_or_magnet=magnet,
                    share_id_or_info_hash=info_hash,
                    size=size, seeders=seeds,
                    quality=quality_display_from_tags(quality_tags), quality_tags=quality_tags,
                    raw={"detail_url": detail_url},
                )
            )
        return results
