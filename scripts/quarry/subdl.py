"""SubDL subtitle search and download client (web scraper — zero config).

Scrapes subdl.com directly to find and download subtitles.
**No API key required** — all data is extracted from public HTML pages.

Scraping flow:
  1. ``GET /search/{query}``  → parse show/movie matches (sd_id, slug, type, year)
  2. ``GET /subtitle/{sd_id}/{slug}``  → list seasons (for TV) or go directly to language
  3. ``GET /subtitle/{sd_id}/{slug}/{season?}/{language}``  → parse subtitle entries with
     direct ``dl.subdl.com`` download links
  4. Download link is ``https://dl.subdl.com/subtitle/{id}-{file_id}.zip`` — no auth
"""
from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import urllib.parse
import urllib.request
import zipfile
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from .common import default_download_dir, safe_filename, storage_root

_BASE = "https://subdl.com"
_DL_BASE = "https://dl.subdl.com"

_DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8,zh;q=0.7",
}

# Season number → SubDL slug mapping
_SEASON_SLUGS = {
    0: "specials-season",
    1: "first-season",
    2: "second-season",
    3: "third-season",
    4: "fourth-season",
    5: "fifth-season",
    6: "sixth-season",
    7: "seventh-season",
    8: "eighth-season",
    9: "ninth-season",
    10: "tenth-season",
    11: "eleventh-season",
    12: "twelfth-season",
    13: "thirteenth-season",
    14: "fourteenth-season",
    15: "fifteenth-season",
    16: "sixteenth-season",
    17: "seventeenth-season",
    18: "eighteenth-season",
    19: "nineteenth-season",
    20: "twentieth-season",
}

# Language code → SubDL URL slug mapping (user-facing code → subdl path segment)
_LANG_SLUGS: dict[str, list[str]] = {
    "zh": ["chinese bg code", "chinese-bg-code", "big_5_code"],
    "en": ["english"],
    "ja": ["japanese"],
    "ko": ["korean"],
    "fr": ["french"],
    "de": ["german"],
    "es": ["spanish"],
    "pt": ["brazillian portuguese", "portuguese"],
    "ar": ["arabic"],
    "ru": ["russian"],
    "it": ["italian"],
    "id": ["indonesian"],
    "vi": ["vietnamese"],
    "th": ["thai"],
    "tr": ["turkish"],
    "fa": ["farsi_persian"],
    "nl": ["dutch"],
    "pl": ["polish"],
    "sv": ["swedish"],
    "da": ["danish"],
    "no": ["norwegian"],
    "fi": ["finnish"],
    "el": ["greek"],
    "ro": ["romanian"],
    "hu": ["hungarian"],
    "cs": ["czech"],
    "bg": ["bulgarian"],
    "hr": ["croatian"],
    "sr": ["serbian"],
    "ms": ["malay"],
    "bn": ["bengali"],
    "he": ["hebrew"],
}

# Regex patterns for parsing HTML
_SD_LINK_RE = re.compile(r'href=["\'](?:https?://subdl\.com)?/subtitle/(sd\d+)/([^"\']+?)["\']', re.I)
_DL_LINK_RE = re.compile(r'href=["\'](?:https?://dl\.subdl\.com)?(/subtitle/[\d]+-[\d]+\.zip)["\']', re.I)
_INFO_LINK_RE = re.compile(r'href=["\'](?:https?://subdl\.com)?/s/info/([^"\']+?)/([^"\']+?)["\']', re.I)
_SEASON_LINK_RE = re.compile(
    r'href=["\'](?:https?://subdl\.com)?/subtitle/sd\d+/[^"\']+?/([a-z]+-season)["\']', re.I,
)
_LANG_LINK_RE = re.compile(
    r'Download\s+(\w[\w\s;,]*?)\s+subtitle\s*\((\d+)\)',
    re.I,
)
_TITLE_YEAR_RE = re.compile(r'^(.+?)\s*\((\d{4})\)\s*$')
_TYPE_RE = re.compile(r'\b(movie|tv)\b', re.I)


def _fetch(url: str, *, timeout: int = 12) -> str:
    """Fetch a URL and return decoded text."""
    req = urllib.request.Request(url, headers=_DEFAULT_HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return str(resp.read().decode("utf-8", errors="replace"))


class SubDLClient:
    """Subtitle search and download via SubDL web scraping.

    **Zero configuration** — no API key, no login, no registration.
    """

    def __init__(self) -> None:
        self.subtitle_dir = storage_root() / "subtitles"
        self.subtitle_dir.mkdir(parents=True, exist_ok=True)

    

    def search(
        self,
        query: str,
        *,
        kind: str = "",
        season: int | None = None,
        episode: int | None = None,
        languages: str = "zh,en",
        limit: int = 10,
    ) -> dict[str, Any]:
        """Search SubDL for subtitles and return structured results.

        Returns a dict with::

            {
                "status": True/False,
                "results": [{"sd_id": ..., "slug": ..., "name": ..., "year": ..., "type": ...}],
                "subtitles": [{"name": ..., "language": ..., "author": ..., "url": ..., "download_url": ...}],
                "error": "..." (if failed)
            }
        """
        try:
            return self._do_search(query, kind=kind, season=season, episode=episode,
                                    languages=languages, limit=limit)
        except Exception as exc:
            return {"status": False, "error": str(exc), "results": [], "subtitles": []}

    def _do_search(
        self,
        query: str,
        *,
        kind: str,
        season: int | None,
        episode: int | None,
        languages: str,
        limit: int,
    ) -> dict[str, Any]:
        # Step 1: Search for shows/movies
        search_url = f"{_BASE}/search/{urllib.parse.quote(query)}"
        html = _fetch(search_url)
        matches = self._parse_search_results(html)

        if not matches:
            return {"status": True, "results": [], "subtitles": []}

        # Pick the best match (prefer exact type match, then most subtitles)
        best = self._pick_best_match(matches, kind=kind)
        if not best:
            return {"status": True, "results": matches, "subtitles": []}

        # Step 2: Determine if this is a TV show
        # Explicit signals: user said kind=tv, or provided a season number
        # Implicit: the parsed type field, or we probe the show page for seasons
        sd_id = best["sd_id"]
        slug = best["slug"]
        is_tv = (
            kind == "tv"
            or season is not None
            or best.get("type") == "tv"
        )

        # If unsure, probe the show page to check for season links
        if not is_tv and not kind:
            try:
                probe_html = _fetch(f"{_BASE}/subtitle/{sd_id}/{slug}")
                probe_seasons = self._parse_seasons(probe_html)
                if probe_seasons:
                    is_tv = True
            except Exception:
                pass

        # Step 3: Build subtitle page URL with language filter
        lang_codes = [l.strip() for l in languages.split(",") if l.strip()]
        all_subtitles: list[dict[str, Any]] = []

        for lang_code in lang_codes:
            lang_slugs = _LANG_SLUGS.get(lang_code, [lang_code])
            for lang_slug in lang_slugs:
                if is_tv and season is not None:
                    season_slug = _SEASON_SLUGS.get(season)
                    if season_slug:
                        sub_url = f"{_BASE}/subtitle/{sd_id}/{slug}/{season_slug}/{urllib.parse.quote(lang_slug)}"
                    else:
                        # Fallback: try numeric season slug
                        sub_url = f"{_BASE}/subtitle/{sd_id}/{slug}/season-{season}/{urllib.parse.quote(lang_slug)}"
                elif is_tv:
                    # No specific season — get the show's main page, then try latest season
                    show_html = _fetch(f"{_BASE}/subtitle/{sd_id}/{slug}")
                    seasons = self._parse_seasons(show_html)
                    if seasons:
                        latest = seasons[-1]
                        sub_url = f"{_BASE}/subtitle/{sd_id}/{slug}/{latest}/{urllib.parse.quote(lang_slug)}"
                    else:
                        sub_url = f"{_BASE}/subtitle/{sd_id}/{slug}/{urllib.parse.quote(lang_slug)}"
                else:
                    # Movie — no season
                    sub_url = f"{_BASE}/subtitle/{sd_id}/{slug}/{urllib.parse.quote(lang_slug)}"

                try:
                    sub_html = _fetch(sub_url)
                    subs = self._parse_subtitle_list(sub_html, lang_code)
                    all_subtitles.extend(subs)
                except Exception:
                    continue  # language may not have subtitles

        # Filter by episode if specified
        if episode is not None:
            ep_patterns = [
                f"S{season or 0:02d}E{episode:02d}",
                f"S{season or 0:02d} E{episode:02d}",
                f"E{episode:02d}",
                f"EP {episode:02d}",
                f"EP{episode:02d}",
                f"EP {episode}",
                f"E{episode}",
            ]
            filtered = []
            for sub in all_subtitles:
                name_upper = sub.get("name", "").upper()
                if any(pat.upper() in name_upper for pat in ep_patterns):
                    filtered.append(sub)
            if filtered:
                all_subtitles = filtered

        # Deduplicate by download URL
        seen_urls: set[str] = set()
        deduped: list[dict[str, Any]] = []
        for sub in all_subtitles:
            dl = sub.get("download_url", "")
            if dl and dl not in seen_urls:
                seen_urls.add(dl)
                deduped.append(sub)
            elif not dl:
                deduped.append(sub)
        all_subtitles = deduped[:limit]

        return {
            "status": True,
            "results": [best],
            "subtitles": all_subtitles,
        }

    

    @staticmethod
    def _parse_search_results(html: str) -> list[dict[str, Any]]:
        """Parse search result page HTML to extract show/movie entries."""
        results: list[dict[str, Any]] = []
        # Find all links to /subtitle/sd{id}/{slug}
        for m in _SD_LINK_RE.finditer(html):
            sd_id = m.group(1)
            slug_raw = m.group(2).rstrip("/")
            # Skip season/language sub-paths
            if "/" in slug_raw:
                continue
            slug = slug_raw

            # Try to extract title, year, type from surrounding context
            # Look for pattern like "Name (Year)typeN subtitles"
            start = max(0, m.start() - 5)
            end = min(len(html), m.end() + 300)
            context = html[start:end]

            name = slug.replace("-", " ").title()
            year = ""
            media_type = ""
            sub_count = 0

            # Try extracting from the visible text after the link
            text_after = context[m.end() - start:]
            # Common pattern: >Title (Year)<...>tv<...>N subtitles
            title_m = re.search(r'>([^<]+?\(\d{4}\))', context)
            if title_m:
                t = title_m.group(1).strip()
                ym = _TITLE_YEAR_RE.match(t)
                if ym:
                    name = ym.group(1).strip()
                    year = ym.group(2)

            type_m = _TYPE_RE.search(text_after[:100])
            if type_m:
                media_type = type_m.group(1).lower()

            count_m = re.search(r'(\d+)\s*subtitles?', text_after[:100], re.I)
            if count_m:
                sub_count = int(count_m.group(1))

            results.append({
                "sd_id": sd_id,
                "slug": slug,
                "name": name,
                "year": year,
                "type": media_type,
                "subtitle_count": sub_count,
            })

        # Deduplicate by sd_id
        seen: set[str] = set()
        deduped: list[dict[str, Any]] = []
        for r in results:
            if r["sd_id"] not in seen:
                seen.add(r["sd_id"])
                deduped.append(r)
        return deduped

    @staticmethod
    def _pick_best_match(matches: list[dict[str, Any]], *, kind: str) -> dict[str, Any] | None:
        """Pick the most relevant search result."""
        if not matches:
            return None
        if len(matches) == 1:
            return matches[0]

        # If kind specified, filter by type
        if kind in ("movie", "tv"):
            typed = [m for m in matches if m.get("type") == kind]
            if typed:
                matches = typed

        # Sort by subtitle count (most first)
        return max(matches, key=lambda m: m.get("subtitle_count", 0))

    @staticmethod
    def _parse_seasons(html: str) -> list[str]:
        """Parse show page to get available season slugs, ordered."""
        seasons: list[str] = []
        for m in _SEASON_LINK_RE.finditer(html):
            slug = m.group(1)
            if slug not in seasons:
                seasons.append(slug)
        return seasons

    @staticmethod
    def _parse_subtitle_list(html: str, lang_code: str = "") -> list[dict[str, Any]]:
        """Parse a subtitle listing page and extract individual subtitle entries."""
        subtitles: list[dict[str, Any]] = []

        # Find all subtitle entries: each has an info link + download link
        # Pattern: link to /s/info/{token}/{slug} followed by author link and Quick Download link
        info_matches = list(_INFO_LINK_RE.finditer(html))
        dl_matches = list(_DL_LINK_RE.finditer(html))

        if not info_matches and not dl_matches:
            return []

        # Build a map of position → download URL
        dl_by_pos: list[tuple[int, str]] = []
        for m in dl_matches:
            dl_path = m.group(1)
            if not dl_path.startswith("http"):
                dl_url = f"{_DL_BASE}{dl_path}"
            else:
                dl_url = dl_path
            dl_by_pos.append((m.start(), dl_url))

        # For each info link, find the nearest following download link
        seen_tokens: set[str] = set()
        for info_m in info_matches:
            token = info_m.group(1)
            if token in seen_tokens:
                continue
            seen_tokens.add(token)

            # Extract subtitle name from context around the info link
            pre_start = max(0, info_m.start() - 200)
            context = html[pre_start:info_m.end() + 500]

            # The name is inside: <a href="/s/info/{token}/..."><h4>NAME</h4></a>
            name = ""
            # Try <h4>...</h4> near the info link first
            h4_m = re.search(
                r'href=["\'][^"\']*' + re.escape(token) + r'[^"\']*["\'][^>]*>'
                r'(?:<h4[^>]*>)?\s*([^<]+)',
                context,
            )
            if h4_m:
                name = h4_m.group(1).strip()

            # Find author from nearby author link
            author = ""
            author_m = re.search(r'/u/([^"\']+)["\']', context[info_m.start() - pre_start:])
            if author_m:
                author = urllib.parse.unquote(author_m.group(1))

            # Find nearest download URL after this info link
            download_url = ""
            for dl_pos, dl_url in dl_by_pos:
                if dl_pos >= info_m.start():
                    download_url = dl_url
                    break

            if name or download_url:
                subtitles.append({
                    "name": name,
                    "language": lang_code or "?",
                    "author": author,
                    "url": f"{_BASE}/s/info/{token}/{info_m.group(2)}",
                    "download_url": download_url,
                })

        return subtitles

    

    def download(self, subtitle_url: str, output_dir: str | None = None) -> list[dict[str, Any]]:
        """Download a subtitle zip and extract .srt/.ass/.vtt files.

        ``subtitle_url`` can be:
        - Full URL: ``https://dl.subdl.com/subtitle/3197651-3213944.zip``
        - Relative path: ``/subtitle/3197651-3213944.zip``
        """
        if subtitle_url.startswith("/"):
            subtitle_url = f"{_DL_BASE}{subtitle_url}"
        elif not subtitle_url.startswith("http"):
            subtitle_url = f"{_DL_BASE}/{subtitle_url}"

        target_dir = Path(output_dir) if output_dir else self.subtitle_dir
        target_dir.mkdir(parents=True, exist_ok=True)

        request = urllib.request.Request(subtitle_url, headers=_DEFAULT_HEADERS)
        with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
            tmp_path = tmp.name
            try:
                with urllib.request.urlopen(request, timeout=30) as response:
                    shutil.copyfileobj(response, tmp)
            except Exception as exc:
                os.unlink(tmp_path)
                raise RuntimeError(f"subtitle download failed: {exc}") from exc

        artifacts: list[dict[str, Any]] = []
        subtitle_exts = {".srt", ".ass", ".ssa", ".vtt", ".sub"}
        try:
            with zipfile.ZipFile(tmp_path, "r") as zf:
                for info in zf.infolist():
                    ext = Path(info.filename).suffix.lower()
                    if ext not in subtitle_exts or info.is_dir():
                        continue
                    safe_name = safe_filename(Path(info.filename).name)
                    out_path = target_dir / safe_name
                    # Avoid overwriting — add numeric suffix
                    counter = 1
                    while out_path.exists():
                        stem = Path(safe_name).stem
                        out_path = target_dir / f"{stem}_{counter}{ext}"
                        counter += 1
                    with zf.open(info) as src, open(out_path, "wb") as dst:
                        shutil.copyfileobj(src, dst)
                    artifacts.append({
                        "path": str(out_path),
                        "size_bytes": out_path.stat().st_size,
                        "original_name": info.filename,
                    })
        finally:
            os.unlink(tmp_path)

        return artifacts


def format_subtitle_results(data: dict[str, Any], artifacts: list[dict[str, Any]] | None = None) -> str:
    """Human-readable formatting of subtitle search results."""
    lines: list[str] = []

    if not data.get("status"):
        lines.append(f"Subtitle search failed: {data.get('error', 'unknown')}")
        return "\n".join(lines)

    results = data.get("results", [])
    subtitles = data.get("subtitles", [])

    if results:
        r = results[0]
        year_str = f" ({r['year']})" if r.get("year") else ""
        type_str = f" [{r.get('type', '')}]" if r.get("type") else ""
        lines.append(f"Subtitle search: {r.get('name', '?')}{year_str}{type_str}")
        lines.append("")

    if not subtitles:
        lines.append("No subtitles found.")
        return "\n".join(lines)

    lines.append(f"Found {len(subtitles)} subtitle(s):")
    for i, sub in enumerate(subtitles[:15], 1):
        lang = sub.get("language", "?")
        author = sub.get("author", "?")
        release = sub.get("name", "?")
        dl_url = sub.get("download_url", "")
        lines.append(f"  {i}. [{lang}] {release}")
        lines.append(f"     by {author}")
        if dl_url:
            lines.append(f"     ↓ {dl_url}")
    lines.append("")

    if artifacts:
        lines.append("Downloaded files:")
        for a in artifacts:
            lines.append(f"  - {a['path']}")

    return "\n".join(lines)
