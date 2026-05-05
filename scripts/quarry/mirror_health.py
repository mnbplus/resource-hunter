"""Mirror health tracking and intelligent rotation for source adapters.

Provides automatic mirror failover with:
- Health-based mirror ordering (fastest/healthiest first)
- 429/503 exponential backoff with cooldown
- SQLite-backed persistent health tracking
- Integration with doctor command for visibility
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

_logger = logging.getLogger(__name__)


@dataclass
class MirrorStatus:
    """Health snapshot of a single mirror."""
    mirror: str
    ok: bool
    latency_ms: int = 0
    last_success_epoch: float = 0.0
    failure_count: int = 0
    backoff_until: float = 0.0  # epoch — skip until this time
    last_error: str = ""


@dataclass(frozen=True)
class MirrorConfig:
    """Mirror configuration for a source adapter."""
    source_name: str
    mirrors: tuple[str, ...]
    path_template: str = ""  # e.g. "/search?q={query}" — appended to mirror base

    def urls(self, **kwargs: str) -> list[str]:
        """Generate full URLs from mirrors + path template."""
        path = self.path_template.format(**kwargs) if self.path_template else ""
        return [f"https://{m}{path}" for m in self.mirrors]


# -- Built-in mirror configurations for sources with known alternatives ------

MIRROR_CONFIGS: dict[str, tuple[str, ...]] = {
    "tpb": (
        "apibay.org",
        "thepiratebay.org",
    ),
    "1337x": (
        "1337x.to",
        "1337x.st",
        "1337x.gd",
        "1337x.so",
    ),
    "torrentgalaxy": (
        "torrentgalaxy.to",
        "torrentgalaxy.mx",
    ),
    "btdig": (
        "btdig.com",
        "btdig.net",
    ),
    "solidtorrents": (
        "solidtorrents.to",
        "solidtorrents.net",
        "solidtorrents.eu",
    ),
    "limetorrents": (
        "limetorrents.lol",
        "limetorrents.pro",
    ),
    "ext_to": (
        "ext.to",
    ),
    "bitsearch": (
        "bitsearch.to",
    ),
    "torlock": (
        "torlock.com",
        "torlock2.com",
    ),
}


class MirrorHealthTracker:
    """Track mirror health and provide smart ordering.

    Uses in-memory tracking (no SQLite dependency for simplicity).
    State resets on process restart — mirrors start fresh each session.
    """

    # Backoff schedule: 30s, 60s, 120s, 300s, 600s max
    _BACKOFF_BASE = 30
    _BACKOFF_MAX = 600

    def __init__(self) -> None:
        # {source_name: {mirror: MirrorStatus}}
        self._status: dict[str, dict[str, MirrorStatus]] = {}

    def _ensure(self, source: str, mirror: str) -> MirrorStatus:
        if source not in self._status:
            self._status[source] = {}
        if mirror not in self._status[source]:
            self._status[source][mirror] = MirrorStatus(mirror=mirror, ok=True)
        return self._status[source][mirror]

    def record_success(self, source: str, mirror: str, latency_ms: int) -> None:
        status = self._ensure(source, mirror)
        status.ok = True
        status.latency_ms = latency_ms
        status.last_success_epoch = time.time()
        status.failure_count = 0
        status.backoff_until = 0.0
        status.last_error = ""

    def record_failure(self, source: str, mirror: str, error: str) -> None:
        status = self._ensure(source, mirror)
        status.ok = False
        status.failure_count += 1
        status.last_error = error[:200]
        # Apply exponential backoff
        backoff = min(
            self._BACKOFF_BASE * (2 ** (status.failure_count - 1)),
            self._BACKOFF_MAX,
        )
        status.backoff_until = time.time() + backoff
        _logger.debug(
            "mirror %s/%s failed (%d), backoff %.0fs: %s",
            source, mirror, status.failure_count, backoff, error[:80],
        )

    def record_rate_limited(self, source: str, mirror: str) -> None:
        """Special handling for 429/503 — longer backoff."""
        status = self._ensure(source, mirror)
        status.failure_count += 1
        # Rate limit gets 2x normal backoff
        backoff = min(
            self._BACKOFF_BASE * 2 * (2 ** (status.failure_count - 1)),
            self._BACKOFF_MAX,
        )
        status.backoff_until = time.time() + backoff
        status.last_error = "rate_limited"
        _logger.info(
            "mirror %s/%s rate-limited, backoff %.0fs",
            source, mirror, backoff,
        )

    def ordered_mirrors(self, source: str, mirrors: tuple[str, ...] | list[str]) -> list[str]:
        """Return mirrors ordered by health: available first, fastest first.

        Mirrors in backoff are moved to the end (but still included as
        last-resort fallback).
        """
        now = time.time()
        available: list[tuple[str, float]] = []
        backed_off: list[tuple[str, float]] = []

        for mirror in mirrors:
            status = self._ensure(source, mirror)
            # Sort key: lower is better
            # Priority: (has recent success, latency)
            sort_key = status.latency_ms if status.ok else 99999
            if status.backoff_until > now:
                backed_off.append((mirror, sort_key))
            else:
                available.append((mirror, sort_key))

        # Sort each group by latency
        available.sort(key=lambda x: x[1])
        backed_off.sort(key=lambda x: x[1])

        return [m for m, _ in available] + [m for m, _ in backed_off]

    def is_rate_limited(self, error: str) -> bool:
        """Check if an error indicates rate limiting."""
        err_lower = error.lower()
        return any(s in err_lower for s in ("429", "503", "rate limit", "too many"))

    def get_source_health(self, source: str) -> list[dict[str, Any]]:
        """Get health status for all known mirrors of a source."""
        if source not in self._status:
            return []
        now = time.time()
        result = []
        for mirror, status in self._status[source].items():
            result.append({
                "mirror": mirror,
                "ok": status.ok,
                "latency_ms": status.latency_ms,
                "failure_count": status.failure_count,
                "in_backoff": status.backoff_until > now,
                "backoff_remaining_s": max(0, int(status.backoff_until - now)),
                "last_error": status.last_error,
            })
        return result

    def summary(self) -> dict[str, Any]:
        """Summary for doctor command."""
        sources: dict[str, Any] = {}
        now = time.time()
        for source, mirrors in self._status.items():
            healthy = sum(1 for s in mirrors.values() if s.ok and s.backoff_until <= now)
            total = len(mirrors)
            sources[source] = {
                "healthy": healthy,
                "total": total,
                "mirrors": self.get_source_health(source),
            }
        return sources


# Global singleton
_tracker = MirrorHealthTracker()


def get_mirror_tracker() -> MirrorHealthTracker:
    return _tracker


__all__ = [
    "MIRROR_CONFIGS",
    "MirrorConfig",
    "MirrorHealthTracker",
    "MirrorStatus",
    "get_mirror_tracker",
]
