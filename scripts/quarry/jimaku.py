"""Jimaku anime subtitle client (web scraper — zero config).

Scrapes jimaku.cc to find Japanese anime subtitles.
**No API key required** — download links are directly available on entry pages.

Flow:
  1. Search: ``GET /`` with text matching, or search via AniList IDs
  2. Entry page: ``GET /entry/{id}`` → parse download links for all files
  3. Download: ``GET /entry/{id}/download/{filename}`` → direct file download

Jimaku specialises in Japanese subtitles for anime (.srt, .ass).
It is the modern successor to Kitsunekko.
"""
from __future__ import annotations

import os
import re
import shutil
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from .common import default_download_dir, safe_filename, storage_root

_BASE = "https://jimaku.cc"

_DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,ja;q=0.8",
}


def _fetch(url: str) -> str:
    """Fetch URL and return response body as text."""
    req = urllib.request.Request(url, headers=_DEFAULT_HEADERS)
    with urllib.request.urlopen(req, timeout=20) as resp:
        return str(resp.read().decode("utf-8", errors="replace"))


# Entry link: /entry/{id} with text = anime name
_ENTRY_LINK_RE = re.compile(
    r'href="/entry/(\d+)"[^>]*>([^<]+)<',
)

# Download link: /entry/{id}/download/{filename}
_DOWNLOAD_LINK_RE = re.compile(
    r'href="(/entry/\d+/download/[^"]+)"[^>]*>([^<]+)<',
)

# Episode number extraction from filename
_EPISODE_RE = re.compile(
    r'(?:S\d+E|EP|E|Episode\s*|第\s*|\s-\s)(\d{1,3})\b',
    re.IGNORECASE,
)


class JimakuClient:
    """Jimaku.cc anime subtitle client."""

    def __init__(self) -> None:
        self.subtitle_dir = Path(storage_root()) / "subtitles"
        self.subtitle_dir.mkdir(parents=True, exist_ok=True)

    def search(
        self,
        query: str,
        *,
        episode: int | None = None,
        limit: int = 15,
    ) -> dict[str, Any]:
        """Search Jimaku for anime subtitles.

        Returns::

            {
                "status": True/False,
                "source": "jimaku",
                "entry": {"id": ..., "name": ...} or None,
                "subtitles": [{"name": ..., "url": ..., "download_url": ..., "episode": ...}],
                "error": "..." (if failed)
            }
        """
        try:
            return self._do_search(query, episode=episode, limit=limit)
        except Exception as exc:
            return {
                "status": False,
                "source": "jimaku",
                "error": str(exc),
                "entry": None,
                "subtitles": [],
            }

    def _do_search(
        self,
        query: str,
        *,
        episode: int | None,
        limit: int,
    ) -> dict[str, Any]:
        # Step 1: Find the best matching entry
        entry = self._find_entry(query)
        if not entry:
            return {
                "status": True,
                "source": "jimaku",
                "entry": None,
                "subtitles": [],
            }

        # Step 2: Fetch the entry page and extract download links
        entry_url = f"{_BASE}/entry/{entry['id']}"
        html = _fetch(entry_url)
        files = self._parse_entry_files(html, entry["id"])

        # Step 3: Filter by episode if specified
        if episode is not None:
            files = [f for f in files if f.get("episode") == episode]

        # Prefer .srt over .ass, and sort by name
        files.sort(key=lambda f: (
            0 if f["name"].lower().endswith(".srt") else 1,
            f["name"],
        ))

        return {
            "status": True,
            "source": "jimaku",
            "entry": entry,
            "subtitles": files[:limit],
        }

    def _find_entry(self, query: str) -> dict[str, Any] | None:
        """Search the Jimaku main page for an anime entry matching the query.

        Jimaku's homepage lists ALL entries alphabetically. We search by fetching
        the main page and matching names. For more targeted search, we can also
        try the entry directly if we know the AniList ID.
        """
        # Strategy: Use the site's built-in search by fetching the page
        # The homepage is a massive listing, but we can try URL-based search
        # Jimaku uses client-side JS search on the homepage, so we try multiple strategies

        # Strategy 1: Try a direct text match on the full listing page
        # The listing page is large (~250KB) but workable
        html = _fetch(_BASE + "/")

        # Normalise query for matching
        query_lower = query.lower().strip()
        query_words = set(query_lower.split())

        entries = list(_ENTRY_LINK_RE.finditer(html))

        best_match: dict[str, Any] | None = None
        best_score: float = 0.0

        for m in entries:
            entry_id = int(m.group(1))
            name = m.group(2).strip()
            name_lower = name.lower()

            # Exact match
            if query_lower == name_lower:
                return {"id": entry_id, "name": name}

            # Substring match
            if query_lower in name_lower:
                score = len(query_lower) / len(name_lower) * 100
                if score > best_score:
                    best_score = score
                    best_match = {"id": entry_id, "name": name}
                continue

            # Word overlap match
            name_words = set(name_lower.split())
            overlap = query_words & name_words
            if overlap:
                score = len(overlap) / max(len(query_words), 1) * 50
                if score > best_score:
                    best_score = score
                    best_match = {"id": entry_id, "name": name}

        return best_match if best_score >= 30 else None

    @staticmethod
    def _parse_entry_files(html: str, entry_id: int) -> list[dict[str, Any]]:
        """Parse an entry page to extract download links and file metadata."""
        files: list[dict[str, Any]] = []
        seen_urls: set[str] = set()

        for m in _DOWNLOAD_LINK_RE.finditer(html):
            path = m.group(1)
            name = urllib.parse.unquote(m.group(2)).strip()

            if path in seen_urls:
                continue
            seen_urls.add(path)

            # Skip non-subtitle files
            ext = Path(name).suffix.lower()
            if ext not in {".srt", ".ass", ".ssa", ".vtt", ".sub"}:
                continue

            # Extract episode number
            ep_match = _EPISODE_RE.search(name)
            episode_num = int(ep_match.group(1)) if ep_match else None

            # Detect language from filename
            language = "ja"  # Default for Jimaku
            name_lower = name.lower()
            if "[chs" in name_lower or "chs," in name_lower or "chinese" in name_lower:
                language = "ja+zh"
            elif "[eng" in name_lower or ".en." in name_lower:
                language = "en"

            download_url = f"{_BASE}{path}"

            files.append({
                "name": name,
                "language": language,
                "source": "jimaku",
                "episode": episode_num,
                "url": f"{_BASE}/entry/{entry_id}",
                "download_url": download_url,
            })

        return files

    def download(
        self,
        download_url: str,
        *,
        output_dir: str | None = None,
    ) -> list[dict[str, Any]]:
        """Download a subtitle file from Jimaku (direct file, not zip)."""
        if not download_url.startswith("http"):
            download_url = f"{_BASE}{download_url}"

        target_dir = Path(output_dir) if output_dir else self.subtitle_dir
        target_dir.mkdir(parents=True, exist_ok=True)

        # Extract filename from URL
        parsed = urllib.parse.urlparse(download_url)
        filename = urllib.parse.unquote(parsed.path.split("/")[-1])
        safe_name = safe_filename(filename)

        out_path = target_dir / safe_name
        counter = 1
        while out_path.exists():
            stem = Path(safe_name).stem
            ext = Path(safe_name).suffix
            out_path = target_dir / f"{stem}_{counter}{ext}"
            counter += 1

        req = urllib.request.Request(download_url, headers=_DEFAULT_HEADERS)
        with urllib.request.urlopen(req, timeout=30) as resp:
            with open(out_path, "wb") as f:
                shutil.copyfileobj(resp, f)

        return [{
            "path": str(out_path),
            "size_bytes": out_path.stat().st_size,
            "original_name": filename,
        }]
