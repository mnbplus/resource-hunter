"""SubHD subtitle search client (web scraper — zero config).

Scrapes subhdtw.com to find Chinese subtitles.
**No API key or login required** for search.

Search flow:
  1. ``GET /search/{query}``  → parse show listing (show_id, show_name)
  2. ``GET /d/{show_id}``     → parse subtitle entries (sid, release_name, uploader)
  3. Download requires captcha — this client focuses on search + metadata

SubHD is the most active Chinese subtitle platform with 简繁双语 coverage.
"""
from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from typing import Any

# SubHD has multiple mirror domains
_BASE = "https://subhdtw.com"

_DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Referer": _BASE + "/",
}


def _fetch(url: str, *, data: bytes | None = None, content_type: str | None = None) -> str:
    """Fetch a URL and return the response body as text."""
    headers = dict(_DEFAULT_HEADERS)
    if content_type:
        headers["Content-Type"] = content_type
    req = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=20) as resp:
        return str(resp.read().decode("utf-8", errors="replace"))


# Show link on search page: /d/{show_id}
_SHOW_LINK_RE = re.compile(r'href="/d/(\d+)"[^>]*>(.*?)</a>', re.DOTALL)

# Subtitle link on show page: /a/{sid}
_SUB_LINK_RE = re.compile(r'href="/a/([A-Za-z0-9]+)"[^>]*>(.*?)</a>', re.DOTALL)

# User link: /u/{username}
_USER_LINK_RE = re.compile(r'href="/u/[^"]*"[^>]*>([^<]+)<')

# Language/format tags in the context after a subtitle link
_LANG_TAG_RE = re.compile(r'<span[^>]*>\s*(简体|繁体|英语|双语|日语|韩语)\s*</span>')
_FORMAT_TAG_RE = re.compile(r'<span[^>]*>\s*(SRT|ASS|SSA)\s*</span>', re.IGNORECASE)


def _strip_html(text: str) -> str:
    """Remove HTML tags from a string."""
    return re.sub(r'<[^>]+>', '', text).strip()


class SubHDClient:
    """SubHD.tw subtitle search client."""

    def search(
        self,
        query: str,
        *,
        season: int | None = None,
        episode: int | None = None,
        limit: int = 15,
    ) -> dict[str, Any]:
        """Search SubHD for Chinese subtitles.

        Returns::

            {
                "status": True/False,
                "source": "subhd",
                "show": {"show_id": ..., "name": ...} or None,
                "subtitles": [{"sid": ..., "name": ..., "uploader": ..., "url": ..., "tags": [...]}],
                "error": "..." (if failed)
            }
        """
        try:
            return self._do_search(query, season=season, episode=episode, limit=limit)
        except Exception as exc:
            return {"status": False, "source": "subhd", "error": str(exc),
                    "show": None, "subtitles": []}

    def _do_search(
        self,
        query: str,
        *,
        season: int | None,
        episode: int | None,
        limit: int,
    ) -> dict[str, Any]:
        # Step 1: Use searchD API (autocomplete) to find shows
        shows = self._search_shows(query)

        if not shows:
            return {"status": True, "source": "subhd", "show": None, "subtitles": []}

        # Pick best show (first one — API already ranks by relevance)
        best_show = shows[0]

        # Step 2: Fetch show detail page for subtitle listing
        show_url = f"{_BASE}/d/{best_show['show_id']}"
        detail_html = _fetch(show_url)
        subtitles = self._parse_subtitle_list(detail_html)

        # Step 3: Filter by season/episode if specified
        if season is not None or episode is not None:
            subtitles = self._filter_by_se(subtitles, season=season, episode=episode)

        return {
            "status": True,
            "source": "subhd",
            "show": best_show,
            "subtitles": subtitles[:limit],
        }

    @staticmethod
    def _search_shows(query: str) -> list[dict[str, Any]]:
        """Search SubHD using the /searchD/ JSON API (autocomplete endpoint).

        Returns list of shows with show_id and name.
        The API returns: {"success": true, "con": "query", "text": "<a ...>...</a>..."}
        """
        search_url = f"{_BASE}/searchD/{urllib.parse.quote(query)}"
        try:
            resp_text = _fetch(search_url)
            data = json.loads(resp_text)
        except Exception:
            return []

        if not data.get("success") or not data.get("text"):
            return []

        # Parse the HTML dropdown items from the "text" field
        html = data["text"]
        shows: list[dict[str, Any]] = []
        seen: set[str] = set()
        for m in _SHOW_LINK_RE.finditer(html):
            show_id = m.group(1)
            if show_id in seen:
                continue
            seen.add(show_id)
            name = _strip_html(m.group(2))
            if name:
                shows.append({"show_id": show_id, "name": name})

        return shows

    @staticmethod
    def _parse_subtitle_list(html: str) -> list[dict[str, Any]]:
        """Parse show detail page to extract individual subtitle entries."""
        subtitles: list[dict[str, Any]] = []
        seen_sids: set[str] = set()

        sub_matches = list(_SUB_LINK_RE.finditer(html))

        for sm in sub_matches:
            sid = sm.group(1)
            if sid in seen_sids:
                continue
            seen_sids.add(sid)

            name = _strip_html(sm.group(2))
            if not name or len(name) < 3:
                continue

            # Extract tags and uploader from context after the link
            post_start = sm.end()
            post_end = min(len(html), post_start + 600)
            context = html[post_start:post_end]

            # Language tags
            lang_matches = _LANG_TAG_RE.findall(context)
            tags = list(dict.fromkeys(lang_matches))  # deduplicate, preserve order

            # Format tags
            fmt_matches = _FORMAT_TAG_RE.findall(context)
            formats = [f.upper() for f in dict.fromkeys(fmt_matches)]

            # Uploader
            user_match = _USER_LINK_RE.search(context)
            uploader = user_match.group(1).strip() if user_match else ""

            subtitles.append({
                "sid": sid,
                "name": name,
                "uploader": uploader,
                "language": "zh",
                "tags": tags,
                "formats": formats,
                "source": "subhd",
                "url": f"{_BASE}/a/{sid}",
                "download_url": "",  # Requires captcha
            })

        return subtitles

    @staticmethod
    def _filter_by_se(
        subtitles: list[dict[str, Any]],
        *,
        season: int | None,
        episode: int | None,
    ) -> list[dict[str, Any]]:
        """Filter subtitles by season/episode markers in their name."""
        filtered: list[dict[str, Any]] = []
        for sub in subtitles:
            text = sub["name"].upper()
            if season is not None:
                s_pats = [f"S{season:02d}", f"S{season}", f"第{season}季"]
                if not any(p.upper() in text for p in s_pats):
                    continue
            if episode is not None:
                e_pats = [f"E{episode:02d}", f"E{episode}", f"第{episode}集",
                          f"EP{episode:02d}", f"EP{episode}",
                          f"EP {episode:02d}", f"EP {episode}"]
                if not any(p.upper() in text for p in e_pats):
                    continue
            filtered.append(sub)
        return filtered

    def get_download_captcha(self, sid: str) -> dict[str, Any]:
        """Request the captcha SVG for a subtitle download."""
        url = f"{_BASE}/api/sub/down"
        body = json.dumps({"sid": sid, "cap": ""}).encode()
        try:
            resp_text = _fetch(url, data=body, content_type="application/json")
            resp = json.loads(resp_text)
            return {
                "success": resp.get("success", False),
                "captcha_svg": resp.get("msg", ""),
                "pass": resp.get("pass", False),
                "url": resp.get("url"),
            }
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    def submit_captcha(self, sid: str, captcha_text: str) -> dict[str, Any]:
        """Submit captcha answer and get the download URL."""
        url = f"{_BASE}/api/sub/down"
        body = json.dumps({"sid": sid, "cap": captcha_text}).encode()
        try:
            resp_text = _fetch(url, data=body, content_type="application/json")
            resp = json.loads(resp_text)
            dl_url = resp.get("url", "")
            return {"success": bool(dl_url), "url": dl_url}
        except Exception as exc:
            return {"success": False, "error": str(exc), "url": None}
