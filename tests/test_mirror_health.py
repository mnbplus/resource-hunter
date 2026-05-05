"""Test mirror health tracking and rotation logic."""
from __future__ import annotations

import time


def test_mirror_ordering_prefers_healthy():
    """Healthy mirrors should sort before failed ones."""
    from quarry.mirror_health import MirrorHealthTracker

    tracker = MirrorHealthTracker()
    mirrors = ("a.com", "b.com", "c.com")

    # Simulate: a.com is slow, b.com is fast, c.com failed
    tracker.record_success("test", "a.com", latency_ms=5000)
    tracker.record_success("test", "b.com", latency_ms=200)
    tracker.record_failure("test", "c.com", error="HTTP 500")

    ordered = tracker.ordered_mirrors("test", mirrors)
    # b.com should come first (fastest), a.com second, c.com last (in backoff)
    assert ordered[0] == "b.com"
    assert ordered[1] == "a.com"
    assert ordered[-1] == "c.com"


def test_rate_limited_mirror_gets_longer_backoff():
    """429/503 errors should get 2x backoff vs regular failures."""
    from quarry.mirror_health import MirrorHealthTracker

    tracker = MirrorHealthTracker()

    # Regular failure: 30s backoff
    tracker.record_failure("test", "regular.com", error="HTTP 500")
    regular_status = tracker._ensure("test", "regular.com")

    # Rate limit: 60s backoff (2x)
    tracker.record_rate_limited("test", "ratelimited.com")
    rl_status = tracker._ensure("test", "ratelimited.com")

    # Rate limited should have longer backoff
    assert rl_status.backoff_until > regular_status.backoff_until


def test_backoff_mirror_still_available_as_last_resort():
    """Mirrors in backoff should still appear in the list (at the end)."""
    from quarry.mirror_health import MirrorHealthTracker

    tracker = MirrorHealthTracker()
    mirrors = ("good.com", "bad.com")

    tracker.record_success("test", "good.com", latency_ms=100)
    tracker.record_failure("test", "bad.com", error="HTTP 500")

    ordered = tracker.ordered_mirrors("test", mirrors)
    assert len(ordered) == 2  # both present
    assert ordered[0] == "good.com"
    assert ordered[1] == "bad.com"


def test_is_rate_limited_detection():
    """Test rate limit error detection."""
    from quarry.mirror_health import MirrorHealthTracker
    tracker = MirrorHealthTracker()
    assert tracker.is_rate_limited("HTTP 429")
    assert tracker.is_rate_limited("HTTP 503")
    assert tracker.is_rate_limited("rate limit exceeded")
    assert tracker.is_rate_limited("Too Many Requests")
    assert not tracker.is_rate_limited("HTTP 500")
    assert not tracker.is_rate_limited("timed out")


def test_summary_report():
    """Summary should include per-source mirror status."""
    from quarry.mirror_health import MirrorHealthTracker

    tracker = MirrorHealthTracker()
    tracker.record_success("src1", "a.com", 100)
    tracker.record_failure("src1", "b.com", "down")
    tracker.record_success("src2", "c.com", 200)

    summary = tracker.summary()
    assert "src1" in summary
    assert "src2" in summary
    assert summary["src1"]["healthy"] == 1
    assert summary["src1"]["total"] == 2
    assert summary["src2"]["healthy"] == 1
