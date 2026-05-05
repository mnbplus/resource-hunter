"""Bangumi Moe (bangumi.moe) torrent source adapter (JSON API).

High-quality Japanese anime torrent site with reliable fansub groups.
Uses the public API at bangumi.moe/api/torrent/search for keyword search.
"""
from __future__ import annotations
import urllib.parse
from .base import HTTPClient, SourceAdapter, TRACKERS, _clean_magnet, _format_size
from ..common import extract_share_id, normalize_title, parse_quality_tags, quality_display_from_tags
from ..exceptions import SourceNetworkError, SourceParseError, SourceUnavailableError
from ..models import SearchIntent, SearchResult


class BangumiMoeSource(SourceAdapter):
    name = "bangumi_moe"
    channel = "torrent"
    priority = 1

    BASE_URL = "https://bangumi.moe"

    def search(self, query: str, intent: SearchIntent, limit: int, page: int, http_client: HTTPClient) -> list[SearchResult]:
        url = f"{self.BASE_URL}/api/torrent/search"
        payload_data = {
            "query": query,
            "p": page,
        }
        headers = {
            "Content-Type": "application/json",
            "Referer": f"{self.BASE_URL}/",
        }

        try:
            resp = http_client.post_json(url, json_data=payload_data, headers=headers)
        except Exception as exc:
            err = str(exc)
            if "500" in err or "502" in err or "503" in err:
                raise SourceUnavailableError(f"bangumi_moe server error: {exc}", source=self.name, url=url) from exc
            raise SourceNetworkError(f"bangumi_moe request failed: {exc}", source=self.name, url=url) from exc

        if not isinstance(resp, dict):
            raise SourceParseError("unexpected response type", source=self.name, url=url)

        torrents = resp.get("torrents", [])
        if not isinstance(torrents, list):
            raise SourceParseError("torrents field is not a list", source=self.name, url=url)

        results: list[SearchResult] = []
        for item in torrents[: max(limit * 3, 12)]:
            title = normalize_title(item.get("title", ""))
            if not title:
                continue

            info_hash = item.get("infoHash", "")
            if not info_hash:
                continue

            magnet = f"magnet:?xt=urn:btih:{info_hash}&dn={urllib.parse.quote(title)}{TRACKERS}"

            size_bytes = item.get("size", 0)
            size_str = _format_size(size_bytes) if size_bytes else ""

            # Bangumi Moe provides leecher/seeder counts sometimes
            seeders = int(item.get("seeders", 0) or 0)

            quality_tags = parse_quality_tags(title)
            pub_date = item.get("publish_time", "")

            # Extract team/fansub group info
            team_name = ""
            team = item.get("team", {})
            if isinstance(team, dict):
                team_name = team.get("name", "")

            results.append(
                SearchResult(
                    channel="torrent", normalized_channel="torrent",
                    source=self.name, upstream_source=self.name, provider="magnet",
                    title=title, link_or_magnet=_clean_magnet(magnet),
                    share_id_or_info_hash=info_hash.lower(),
                    size=size_str,
                    seeders=seeders,
                    quality=quality_display_from_tags(quality_tags), quality_tags=quality_tags,
                    raw={"title": title, "team": team_name, "pub_date": pub_date},
                )
            )
        return results
