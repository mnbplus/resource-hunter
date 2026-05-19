from quarry.intent import parse_intent
from quarry.ranking import deduplicate_results, score_result
from quarry.models import SearchIntent, SearchPlan, SearchResult
from quarry.cache import ResourceCache
from quarry.models import SourceStatus
from quarry.engine import ResourceHunterEngine
from quarry.sources.base import SourceAdapter


def test_pan_dedup_prefers_result_with_password():
    first = SearchResult(
        channel="pan",
        source="2fun",
        provider="aliyun",
        title="Movie A",
        link_or_magnet="https://example.com/share/abc",
        share_id_or_info_hash="abc",
        password="",
    )
    second = SearchResult(
        channel="pan",
        source="pansou",
        provider="aliyun",
        title="Movie A mirror",
        link_or_magnet="https://example.com/share/abc?pwd=1234",
        share_id_or_info_hash="abc",
        password="1234",
    )
    deduped = deduplicate_results([first, second])
    assert len(deduped) == 1
    assert deduped[0].password == "1234"


def test_torrent_score_rewards_match_quality_and_seeders():
    intent = parse_intent("Oppenheimer 2023", wants_4k=True)
    result = SearchResult(
        channel="torrent",
        source="yts",
        provider="magnet",
        title="Oppenheimer 2023 2160p HDR",
        link_or_magnet="magnet:?xt=urn:btih:abc",
        share_id_or_info_hash="abc",
        seeders=88,
    )
    scored = score_result(result, intent)
    assert scored.score > 80
    assert "4k requested" in scored.reasons
    assert any(r.startswith("seeders") for r in scored.reasons)


def test_pan_probe_loaded_page_without_positive_signal_is_unknown(monkeypatch):
    from quarry import pan_probe

    monkeypatch.setattr(pan_probe, "_get_text", lambda url: "x" * 800)

    result = pan_probe._probe_baidu("https://pan.baidu.com/s/1abcdefghijk")

    assert result.alive is None
    assert result.reason == "page loaded without positive share signal"


def test_source_health_metrics_include_result_yield(tmp_path):
    cache = ResourceCache(tmp_path / "cache.db")
    cache.record_source_status(SourceStatus(source="nyaa", kind="anime", channel="torrent", priority=1, ok=True, latency_ms=100))
    cache.record_source_status(SourceStatus(source="nyaa", kind="anime", channel="torrent", priority=1, ok=True, latency_ms=300))
    cache.record_source_result_metrics(
        source="nyaa",
        kind="anime",
        channel="torrent",
        result_count=6,
        confident_count=4,
        top_hit=True,
    )

    metrics = cache.source_health_metrics("nyaa", kind="anime")

    assert metrics["success_rate_24h"] == 1.0
    assert metrics["median_latency_ms"] == 200
    assert metrics["result_yield"] == 6.0
    assert metrics["avg_confident_results"] == 4.0
    assert metrics["top_hit_rate"] == 1.0
    assert metrics["recommended_query_budget"] == 3


def test_adaptive_query_budget_uses_source_health_metrics(tmp_path):
    cache = ResourceCache(tmp_path / "cache.db")
    engine = ResourceHunterEngine(cache=cache)
    cache.record_source_status(SourceStatus(source="slow_source", kind="movie", channel="torrent", priority=1, ok=True, latency_ms=500))
    cache.record_source_result_metrics(
        source="slow_source",
        kind="movie",
        channel="torrent",
        result_count=0,
        confident_count=0,
        top_hit=False,
    )

    assert engine._query_budget_for_source("slow_source", "movie", profile_budget=3, degraded=False) == 1
    assert engine._query_budget_for_source("unknown_source", "movie", profile_budget=3, degraded=False) == 3


def test_adaptive_source_health_can_reorder_preferred_sources(tmp_path):
    class SlowPreferredSource(SourceAdapter):
        name = "slow_preferred"
        channel = "torrent"
        priority = 1

        def search(self, query, intent, limit, page, http_client):
            return []

    class FastSecondSource(SourceAdapter):
        name = "fast_second"
        channel = "torrent"
        priority = 1

        def search(self, query, intent, limit, page, http_client):
            return []

    cache = ResourceCache(tmp_path / "cache.db")
    engine = ResourceHunterEngine(cache=cache)
    engine.torrent_sources = [SlowPreferredSource(), FastSecondSource()]
    intent = SearchIntent(
        query="movie",
        original_query="movie",
        kind="movie",
        channel="torrent",
        title_core="movie",
        title_tokens=["movie"],
    )
    plan = SearchPlan(
        channels=["torrent"],
        torrent_queries=["movie"],
        preferred_torrent_sources=["slow_preferred", "fast_second"],
    )
    cache.record_source_status(SourceStatus(source="slow_preferred", kind="movie", channel="torrent", priority=1, ok=True, latency_ms=2500))
    cache.record_source_result_metrics(
        source="slow_preferred",
        kind="movie",
        channel="torrent",
        result_count=0,
        confident_count=0,
        top_hit=False,
    )
    cache.record_source_status(SourceStatus(source="fast_second", kind="movie", channel="torrent", priority=1, ok=True, latency_ms=10))
    cache.record_source_result_metrics(
        source="fast_second",
        kind="movie",
        channel="torrent",
        result_count=8,
        confident_count=6,
        top_hit=True,
    )

    ordered = engine._ordered_sources("torrent", plan, intent)

    assert [source.name for source in ordered] == ["fast_second", "slow_preferred"]


def test_cache_cleanup_removes_old_source_result_metrics(tmp_path):
    cache = ResourceCache(tmp_path / "cache.db")
    cache.record_source_result_metrics(
        source="nyaa",
        kind="anime",
        channel="torrent",
        result_count=6,
        confident_count=4,
        top_hit=True,
    )
    with cache._connect() as conn:
        conn.execute("update source_result_metrics set recorded_epoch = 1")

    deleted = cache.cleanup(max_age_seconds=60)
    metrics = cache.source_health_metrics("nyaa", kind="anime")

    assert deleted["source_result_metrics"] == 1
    assert metrics["result_yield"] == 0.0
