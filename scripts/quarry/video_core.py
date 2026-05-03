from __future__ import annotations


import hashlib
import json
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Callable

from .cache import ResourceCache
from .common import default_download_dir, detect_platform, safe_filename, storage_root
from .models import VideoResult

ProgressCallback = Callable[[dict[str, Any]], None]


class VideoManager:
    def __init__(self, cache: ResourceCache | None = None) -> None:
        self.cache = cache or ResourceCache()
        self.download_dir = default_download_dir()
        self.subtitle_dir = storage_root() / "subtitles"
        self.subtitle_dir.mkdir(parents=True, exist_ok=True)

    def _binary_status(self) -> dict[str, Any]:
        return {
            "yt_dlp": shutil.which("yt-dlp"),
            "ffmpeg": shutil.which("ffmpeg"),
        }

    def _task_id(self, url: str, action: str, preset: str = "") -> str:
        digest = hashlib.sha1(f"{url}|{action}|{preset}".encode("utf-8")).hexdigest()[:8]
        return f"{int(time.time())}-{digest}"

    def _run_ytdlp(self, args: list[str], capture: bool = True, timeout: int = 300) -> subprocess.CompletedProcess[str]:
        binary = shutil.which("yt-dlp")
        if not binary:
            raise RuntimeError("yt-dlp not found")
        return subprocess.run(
            [binary] + args,
            capture_output=capture, text=True, timeout=timeout,
            encoding="utf-8", errors="replace",
        )

    _PROGRESS_RE = re.compile(r'\[download\]\s+(?P<pct>[\d.]+)%\s+of')

    def _run_ytdlp_streaming(
        self, args: list[str], progress_callback: ProgressCallback | None = None, timeout: int = 600,
    ) -> tuple[int, str, str]:
        """Run yt-dlp with real-time progress parsing via Popen."""
        binary = shutil.which("yt-dlp")
        if not binary:
            raise RuntimeError("yt-dlp not found")
        proc = subprocess.Popen(
            [binary] + args,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace",
        )
        stdout_lines: list[str] = []
        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                stdout_lines.append(line)
                if progress_callback:
                    match = self._PROGRESS_RE.search(line)
                    if match:
                        progress_callback({"percent": float(match.group("pct")), "line": line.rstrip()})
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            raise RuntimeError("download timed out")
        stderr = proc.stderr.read() if proc.stderr else ""
        return proc.returncode, "".join(stdout_lines), stderr

    def _load_info_json(self, url: str) -> dict[str, Any]:
        result = self._run_ytdlp(["-J", "--no-playlist", url], capture=True, timeout=120)
        if result.returncode != 0:
            raise RuntimeError((result.stderr or result.stdout or "yt-dlp failed").strip()[:240])
        info: dict[str, Any] = json.loads(result.stdout)
        return info

    def _format_entries(self, data: dict[str, Any]) -> list[dict[str, Any]]:
        formats: list[dict[str, Any]] = []
        seen: set[tuple[str, int | None]] = set()
        for item in data.get("formats", []):
            height = item.get("height")
            format_id = item.get("format_id")
            if not format_id:
                continue
            key = (format_id, height)
            if key in seen:
                continue
            seen.add(key)
            filesize = item.get("filesize") or item.get("filesize_approx") or 0
            formats.append(
                {
                    "id": format_id,
                    "ext": item.get("ext"),
                    "height": height,
                    "width": item.get("width"),
                    "has_audio": item.get("acodec") not in (None, "none"),
                    "has_video": item.get("vcodec") not in (None, "none"),
                    "filesize_mb": round(filesize / 1024 / 1024, 2) if filesize else None,
                    "note": item.get("format_note") or item.get("format"),
                }
            )
        formats.sort(key=lambda item: ((item.get("height") or 0), item.get("has_audio"), item["id"]), reverse=True)
        return formats

    def _recommended(self, formats: list[dict[str, Any]]) -> list[dict[str, Any]]:
        best = next((item for item in formats if item.get("has_video")), None)
        balanced = next((item for item in formats if (item.get("height") or 0) <= 1080 and item.get("has_video")), best)
        small = next((item for item in formats if (item.get("height") or 0) <= 720 and item.get("has_video")), balanced)
        return [
            {"preset": "best", "format": best["id"] if best else "best"},
            {"preset": "balanced", "format": balanced["id"] if balanced else "best"},
            {"preset": "small", "format": small["id"] if small else "best"},
            {"preset": "audio", "format": "bestaudio/best"},
        ]

    def info(self, url: str) -> VideoResult:
        data = self._load_info_json(url)
        formats = self._format_entries(data)
        return VideoResult(
            url=url,
            platform=detect_platform(url),
            title=data.get("title", ""),
            duration=data.get("duration"),
            formats=formats,
            recommended=self._recommended(formats),
            meta={
                "uploader": data.get("uploader"),
                "yt_dlp": self._binary_status()["yt_dlp"],
                "ffmpeg": self._binary_status()["ffmpeg"],
            },
        )

    def probe(self, url: str) -> VideoResult:
        info = self.info(url)
        info.formats = info.formats[:6]
        info.meta["probe"] = True
        return info

    def _preset_expression(self, preset: str) -> tuple[str, bool]:
        if preset == "best":
            return "bestvideo[ext=mp4]+bestaudio[ext=m4a]/bestvideo+bestaudio/best", False
        if preset == "balanced":
            return "bestvideo[height<=1080]+bestaudio/best[height<=1080]/best", False
        if preset == "small":
            return "best[height<=720]/best", False
        if preset == "audio":
            return "bestaudio/best", True
        return preset, False

    def _artifacts_for_prefix(self, directory: Path, prefix: str) -> list[Path]:
        return sorted(directory.glob(f"{prefix}*"), key=lambda path: path.stat().st_mtime)

    def download(
        self, url: str, preset: str = "best", output_dir: str | None = None,
        progress_callback: ProgressCallback | None = None,
    ) -> VideoResult:
        target_dir = Path(output_dir) if output_dir else self.download_dir
        target_dir.mkdir(parents=True, exist_ok=True)
        task_id = self._task_id(url, "download", preset)
        prefix = safe_filename(f"rh_{task_id}_")
        template = str(target_dir / f"{prefix}%(title)s.%(ext)s")
        before_files = {path.resolve() for path in target_dir.glob("*") if path.is_file()}
        format_expr, audio_only = self._preset_expression(preset)
        args = [
            "--no-playlist", "--no-warnings",
            "--print", "after_move:filepath",
            "-o", template, "-f", format_expr,
        ]
        if audio_only:
            args.extend(["-x", "--audio-format", "mp3"])
        elif self._binary_status()["ffmpeg"]:
            args.extend(["--merge-output-format", "mp4"])
        args.append(url)
        if progress_callback:
            returncode, stdout, stderr = self._run_ytdlp_streaming(args, progress_callback=progress_callback, timeout=600)
        else:
            result = self._run_ytdlp(args, capture=True, timeout=600)
            returncode, stdout, stderr = result.returncode, result.stdout, result.stderr
        if returncode != 0:
            raise RuntimeError((stderr or stdout or "download failed").strip()[:240])
        artifact_paths = [
            Path(line.strip())
            for line in stdout.splitlines()
            if line.strip() and Path(line.strip()).exists()
        ]
        if not artifact_paths:
            after_files = {path.resolve() for path in target_dir.glob("*") if path.is_file()}
            artifact_paths = [Path(path) for path in sorted(after_files - before_files)]
        if not artifact_paths:
            artifact_paths = self._artifacts_for_prefix(target_dir, prefix)
        artifacts = [
            {
                "path": str(path),
                "size_bytes": path.stat().st_size,
                "preset": preset,
                "task_id": task_id,
                "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
            for path in artifact_paths
        ]
        payload = {
            "task_id": task_id,
            "url": url,
            "preset": preset,
            "output_dir": str(target_dir),
            "artifacts": artifacts,
            "meta": self._binary_status(),
        }
        self.cache.record_video_manifest(task_id, url, payload)
        return VideoResult(
            url=url,
            platform=detect_platform(url),
            title=artifact_paths[0].name if artifact_paths else "",
            artifacts=artifacts,
            meta={**dict(payload.get("meta", {})), "task_id": task_id},  # type: ignore[arg-type]
        )

    def subtitle(self, url: str, lang: str = "zh-Hans,zh,en") -> VideoResult:
        task_id = self._task_id(url, "subtitle", lang)
        prefix = safe_filename(f"rh_{task_id}_")
        template = str(self.subtitle_dir / f"{prefix}%(title)s")
        before_files = {path.resolve() for path in self.subtitle_dir.glob("*.vtt")}
        result = self._run_ytdlp(
            [
                "--skip-download",
                "--write-auto-sub",
                "--write-sub",
                "--sub-lang",
                lang,
                "--sub-format",
                "vtt",
                "-o",
                template,
                url,
            ],
            capture=True,
            timeout=300,
        )
        if result.returncode != 0:
            raise RuntimeError((result.stderr or result.stdout or "subtitle failed").strip()[:240])
        after_files = {path.resolve() for path in self.subtitle_dir.glob("*.vtt")}
        subtitle_files = [Path(path) for path in sorted(after_files - before_files)]
        if not subtitle_files:
            subtitle_files = self._artifacts_for_prefix(self.subtitle_dir, prefix)
        cleaned_lines: list[str] = []
        seen_lines: set[str] = set()
        artifacts: list[dict[str, Any]] = []
        for subtitle_file in subtitle_files:
            text = subtitle_file.read_text(encoding="utf-8", errors="replace")
            for line in text.splitlines():
                stripped = line.strip()
                if not stripped or stripped == "WEBVTT" or "-->" in stripped or stripped.isdigit():
                    continue
                if stripped in seen_lines:
                    continue
                seen_lines.add(stripped)
                cleaned_lines.append(stripped)
            artifacts.append({"path": str(subtitle_file), "size_bytes": subtitle_file.stat().st_size, "task_id": task_id})
        cleaned_text = "\n".join(cleaned_lines)[:5000]
        payload = {
            "task_id": task_id,
            "url": url,
            "lang": lang,
            "artifacts": artifacts,
            "meta": {"text": cleaned_text, **self._binary_status()},
        }
        self.cache.record_video_manifest(task_id, url, payload)
        return VideoResult(
            url=url,
            platform=detect_platform(url),
            artifacts=artifacts,
            meta={"lang": lang, "text": cleaned_text, "task_id": task_id, **self._binary_status()},
        )

    def doctor(self) -> dict[str, Any]:
        return {
            "schema_version": "3",
            "binaries": self._binary_status(),
            "download_dir": str(self.download_dir),
            "subtitle_dir": str(self.subtitle_dir),
            "recent_manifests": self.cache.list_video_manifests(limit=5),
        }


def format_video_text(result: VideoResult, mode: str) -> str:
    lines = [f"Video {mode}", f"URL: {result.url}", f"Platform: {result.platform}"]
    if result.title:
        lines.append(f"Title: {result.title}")
    if result.duration:
        minutes, seconds = divmod(int(result.duration), 60)
        lines.append(f"Duration: {minutes}:{seconds:02d}")
    if result.meta.get("task_id"):
        lines.append(f"Task: {result.meta['task_id']}")
    if result.formats:
        lines.append("")
        lines.append("Formats:")
        for entry in result.formats[:10]:
            bits = [entry["id"]]
            if entry.get("height"):
                bits.append(f"{entry['height']}p")
            if entry.get("ext"):
                bits.append(entry["ext"])
            if entry.get("filesize_mb"):
                bits.append(f"{entry['filesize_mb']}MB")
            lines.append("- " + " | ".join(bits))
    if result.recommended:
        lines.append("")
        lines.append("Recommended:")
        for item in result.recommended:
            lines.append(f"- {item['preset']}: {item['format']}")
    if result.artifacts:
        lines.append("")
        lines.append("Artifacts:")
        for artifact in result.artifacts:
            lines.append(f"- {artifact['path']}")
    text = result.meta.get("text")
    if text:
        lines.append("")
        lines.append("Subtitle preview:")
        lines.append(text[:1000])
    return "\n".join(lines)
