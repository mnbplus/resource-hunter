"""動漫花園 (share.dmhy.org) torrent source adapter (RSS).

Premier Chinese-language anime/subtitle-group community. Covers anime,
music, manga, drama, and tokusatsu. Extremely fast release cycle for
seasonal anime with Chinese subs.
"""
from __future__ import annotations
import re
import urllib.parse
from xml.etree import ElementTree
from .base import HTTPClient, SourceAdapter, TRACKERS, _clean_magnet
from ..common import extract_share_id, normalize_title, parse_quality_tags, quality_display_from_tags
from ..models import SearchIntent, SearchResult

_MAGNET_HASH_RE = re.compile(r"btih:([0-9a-fA-F]{32,40})", re.I)


class DMHYSource(SourceAdapter):
    name = "dmhy"
    channel = "torrent"
    priority = 1

    BASE_URL = "https://share.dmhy.org"

    # DMHY category IDs:  2=anime, 12=music, 28=manga, 31=anime-raw, etc.
    _KIND_TO_CATEGORY = {
        "anime": "2",
        "music": "12",
    }

    def search(self, query: str, intent: SearchIntent, limit: int, page: int, http_client: HTTPClient) -> list[SearchResult]:
        params: dict[str, str] = {
            "keyword": query,
        }
        cat = self._KIND_TO_CATEGORY.get(intent.kind, "")
        if cat:
            params["sort_id"] = cat

        qs = urllib.parse.urlencode(params)
        url = f"{self.BASE_URL}/topics/rss/rss.xml?{qs}"
        payload = http_client.get_text(url)
        root = ElementTree.fromstring(payload)

        results: list[SearchResult] = []
        for item in root.findall("./channel/item")[: max(limit * 3, 12)]:
            title = normalize_title(item.findtext("title", ""))
            if not title:
                continue

            # DMHY RSS provides enclosure with .torrent URL and sometimes magnet in link
            magnet = ""
            info_hash = ""

            # Try enclosure first (usually .torrent URL)
            enclosure = item.find("enclosure")
            enclosure_url = enclosure.get("url", "") if enclosure is not None else ""

            # Try link tag — DMHY sometimes puts magnet here
            link_text = item.findtext("link", "")
            if link_text and link_text.startswith("magnet:"):
                magnet = link_text

            # If no magnet found, try to extract from description or enclosure
            if not magnet:
                desc = item.findtext("description", "")
                magnet_match = re.search(r'href="(magnet:\?[^"]+)"', desc)
                if magnet_match:
                    magnet = magnet_match.group(1)

            # Extract info_hash from magnet or enclosure URL
            if magnet:
                info_hash = extract_share_id(magnet, provider_hint="magnet")
            elif enclosure_url:
                hash_match = _MAGNET_HASH_RE.search(enclosure_url)
                if hash_match:
                    info_hash = hash_match.group(1).lower()
                    magnet = f"magnet:?xt=urn:btih:{info_hash}&dn={urllib.parse.quote(title)}{TRACKERS}"

            # Fallback: use .torrent URL directly
            final_link = _clean_magnet(magnet) if magnet else enclosure_url
            if not final_link:
                continue

            quality_tags = parse_quality_tags(title)
            pub_date = item.findtext("pubDate", "")

            results.append(
                SearchResult(
                    channel="torrent", normalized_channel="torrent",
                    source=self.name, upstream_source=self.name, provider="magnet" if magnet else "torrent",
                    title=title, link_or_magnet=final_link,
                    share_id_or_info_hash=info_hash,
                    size="",
                    seeders=0,
                    quality=quality_display_from_tags(quality_tags), quality_tags=quality_tags,
                    raw={"title": title, "pub_date": pub_date},
                )
            )
        return results
