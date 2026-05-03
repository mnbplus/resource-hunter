from __future__ import annotations

from quarry.cache import ResourceCache
from quarry.cli import main as cli_main
from quarry.common import parse_quality_tags
from quarry.engine import ResourceHunterEngine
from quarry.intent import parse_intent
from quarry.sources.base import SourceAdapter
from quarry.intent import AliasResolver
from quarry.models import SearchResult, SourceStatus
from quarry.ranking import source_is_degraded
from quarry.rendering import format_search_text
from quarry.sources import _flatten_pan_payload


class FakeSource(SourceAdapter):
    def __init__(self, name: str, channel: str, priority: int, results: list[SearchResult]) -> None:
        self.name = name
        self.channel = channel
        self.priority = priority
        self._results = results

    def search(self, query, intent, limit, page, http_client):
        return list(self._results)


def test_title_core_extraction_for_tv_query():
    intent = parse_intent("Breaking Bad S01E01")
    assert intent.kind == "tv"
    assert intent.title_core == "breaking bad"
    assert intent.title_tokens == ["breaking", "bad"]


def test_remux_is_not_treated_as_4k():
    tags = parse_quality_tags("Oppenheimer 2023 1080p REMUX HDR")
    assert tags["resolution"] == "1080p"
    assert tags["pack"] == "remux"


def test_engine_ranks_exact_title_episode_above_episode_only(tmp_path):
    cache = ResourceCache(tmp_path / "cache.db")
    engine = ResourceHunterEngine(cache=cache)
    exact = SearchResult(
        channel="torrent",
        source="tpb",
        provider="magnet",
        title="Breaking Bad S01E01 Pilot 1080p WEB-DL",
        link_or_magnet="magnet:?xt=urn:btih:aaa",
        share_id_or_info_hash="aaa",
        seeders=12,
    )
    unrelated = SearchResult(
        channel="torrent",
        source="eztv",
        provider="magnet",
        title="Steel Ball Run S01E01 1080p WEB h264",
        link_or_magnet="magnet:?xt=urn:btih:bbb",
        share_id_or_info_hash="bbb",
        seeders=99,
    )
    engine.pan_sources = []
    engine.torrent_sources = [FakeSource("tpb", "torrent", 2, [exact]), FakeSource("eztv", "torrent", 1, [unrelated])]
    response = engine.search(parse_intent("Breaking Bad S01E01", explicit_kind="tv"), use_cache=False)
    assert response["results"][0]["title"].startswith("Breaking Bad")
    assert response["results"][0]["match_bucket"] == "exact_title_episode"
    weak = [item for item in response["results"] if item["title"].startswith("Steel Ball Run")][0]
    assert weak["match_bucket"] == "episode_only_match"


def test_degraded_sources_stay_enabled_but_rank_lower(monkeypatch, tmp_path):
    from quarry.sources.base import SourceRuntimeProfile

    def fake_profile(name: str) -> SourceRuntimeProfile:
        if name == "ps.252035":
            return SourceRuntimeProfile(
                supported_kinds=("movie", "tv", "anime", "music", "software", "book", "general"),
                timeout=8, retries=0, degraded_score_penalty=10, cooldown_seconds=90,
                failure_threshold=1, query_budget=2, default_degraded=True,
            )
        return SourceRuntimeProfile(
            supported_kinds=("movie", "tv", "anime", "music", "software", "book", "general"),
            timeout=10, retries=0, degraded_score_penalty=6, cooldown_seconds=120,
            failure_threshold=2, query_budget=2, default_degraded=False,
        )

    monkeypatch.setattr("quarry.ranking.profile_for", fake_profile)
    monkeypatch.setattr("quarry.engine.profile_for", fake_profile)

    cache = ResourceCache(tmp_path / "cache.db")
    engine = ResourceHunterEngine(cache=cache)
    stable = SearchResult(
        channel="pan",
        source="upyunso",
        provider="aliyun",
        title="Oppenheimer 2023 2160p HDR",
        link_or_magnet="https://example.com/share/111",
        share_id_or_info_hash="111",
    )
    degraded = SearchResult(
        channel="pan",
        source="ps.252035",
        provider="aliyun",
        title="Oppenheimer 2023 2160p HDR",
        link_or_magnet="https://example.com/share/222",
        share_id_or_info_hash="222",
    )
    engine.torrent_sources = []
    engine.pan_sources = [FakeSource("upyunso", "pan", 1, [stable]), FakeSource("ps.252035", "pan", 3, [degraded])]
    response = engine.search(parse_intent("Oppenheimer 2023", explicit_kind="movie"), use_cache=False)
    assert response["results"][0]["source"] == "upyunso"
    assert [item["source"] for item in response["results"]] == ["upyunso"]


def test_cli_text_limit_is_hard_contract(monkeypatch, capsys):
    fake_response = {
        "query": "test query",
        "intent": {"kind": "general", "quick": False},
        "plan": {"channels": ["pan", "torrent"], "notes": ["demo"]},
        "results": [
            {
                "channel": "pan",
                "source": "2fun",
                "provider": "aliyun",
                "title": "Demo One",
                "link_or_magnet": "https://example.com/1",
                "password": "",
                "share_id_or_info_hash": "1",
                "size": "",
                "seeders": 0,
                "quality": "",
                "quality_tags": {},
                "score": 77,
                "reasons": ["query match"],
                "penalties": [],
                "match_bucket": "title_family_match",
                "confidence": 0.7,
                "source_degraded": False,
                "raw": {},
            },
            {
                "channel": "pan",
                "source": "2fun",
                "provider": "aliyun",
                "title": "Demo Two",
                "link_or_magnet": "https://example.com/2",
                "password": "",
                "share_id_or_info_hash": "2",
                "size": "",
                "seeders": 0,
                "quality": "",
                "quality_tags": {},
                "score": 60,
                "reasons": ["query match"],
                "penalties": [],
                "match_bucket": "weak_context_match",
                "confidence": 0.2,
                "source_degraded": False,
                "raw": {},
            },
        ],
        "warnings": [],
        "source_status": [],
        "meta": {"cached": False},
    }

    monkeypatch.setattr("quarry.cli.ResourceHunterEngine.search", lambda *args, **kwargs: fake_response)
    rc = cli_main(["search", "test query", "--limit", "1"])
    assert rc == 0
    output = capsys.readouterr().out
    assert "Demo One" in output
    assert "Demo Two" not in output


def test_json_retains_weak_candidates_with_bucket(tmp_path):
    cache = ResourceCache(tmp_path / "cache.db")
    engine = ResourceHunterEngine(cache=cache)
    strong = SearchResult(
        channel="torrent",
        source="tpb",
        provider="magnet",
        title="Breaking Bad S01E01 Pilot 1080p",
        link_or_magnet="magnet:?xt=urn:btih:111",
        share_id_or_info_hash="111",
        seeders=4,
    )
    weak = SearchResult(
        channel="torrent",
        source="eztv",
        provider="magnet",
        title="Random Show S01E01 720p",
        link_or_magnet="magnet:?xt=urn:btih:222",
        share_id_or_info_hash="222",
        seeders=80,
    )
    engine.pan_sources = []
    engine.torrent_sources = [FakeSource("tpb", "torrent", 2, [strong]), FakeSource("eztv", "torrent", 1, [weak])]
    response = engine.search(parse_intent("Breaking Bad S01E01", explicit_kind="tv"), use_cache=False)
    assert response["meta"]["candidate_count"] == 2
    weak_result = [item for item in response["results"] if item["title"].startswith("Random Show")][0]
    assert weak_result["match_bucket"] in {"episode_only_match", "weak_context_match"}
    assert weak_result["confidence"] < 0.5


def test_pan_aggregated_magnet_normalizes_to_torrent_channel():
    payload = {
        "results": [
            {
                "title": "Test Movie",
                "url": "magnet:?xt=urn:btih:abc",
                "netdiskType": "magnet",
            }
        ]
    }
    results = _flatten_pan_payload(payload, "2fun")
    assert results[0].channel == "torrent"
    assert results[0].source == "2fun"


def test_titles_that_only_mention_target_do_not_enter_exact_bucket(tmp_path):
    cache = ResourceCache(tmp_path / "cache.db")
    engine = ResourceHunterEngine(cache=cache)
    exact = SearchResult(
        channel="torrent",
        source="tpb",
        provider="magnet",
        title="Breaking Bad S01E01 Pilot 1080p WEB-DL",
        link_or_magnet="magnet:?xt=urn:btih:aaa",
        share_id_or_info_hash="aaa",
    )
    mention = SearchResult(
        channel="torrent",
        source="tpb",
        provider="magnet",
        title="The Writers Room 2013 S01E01 Breaking Bad 720p HDTV",
        link_or_magnet="magnet:?xt=urn:btih:bbb",
        share_id_or_info_hash="bbb",
    )
    engine.pan_sources = []
    engine.torrent_sources = [FakeSource("tpb", "torrent", 2, [exact, mention])]
    response = engine.search(parse_intent("Breaking Bad S01E01", explicit_kind="tv"), use_cache=False)
    # The mention-only title may land in results or suppressed depending on scoring;
    # the key invariant is it must never get exact_title_episode bucket.
    all_items = response["results"] + response.get("suppressed", [])
    mention_result = [item for item in all_items if item["title"].startswith("The Writers Room")][0]
    assert mention_result["match_bucket"] != "exact_title_episode"


def test_alias_resolution_for_chinese_old_movie_drives_plan_and_confidence(monkeypatch, tmp_path):
    cache = ResourceCache(tmp_path / "cache.db")
    engine = ResourceHunterEngine(cache=cache)

    def fake_resolve(self, intent, cache, http_client):
        return {
            "original_title": "赤橙黄绿青蓝紫",
            "english_title": "Full of Colors",
            "romanized_title": "Chi cheng huang lü qing lan zi",
            "alternate_titles": ["3-Dimensional People"],
            "resolved_year": "1982",
            "resolver_sources": ["https://example.com/movie"],
        }

    weak = SearchResult(
        channel="torrent",
        source="tpb",
        provider="magnet",
        title="Blade Runner 1982 1080p BluRay",
        link_or_magnet="magnet:?xt=urn:btih:111",
        share_id_or_info_hash="111",
        seeders=99,
    )
    engine.pan_sources = []
    engine.torrent_sources = [FakeSource("tpb", "torrent", 2, [weak])]
    monkeypatch.setattr(AliasResolver, "resolve", fake_resolve)
    response = engine.search(parse_intent("赤橙黄绿青蓝紫 1982", explicit_kind="movie"), use_cache=False)
    assert "Full of Colors" in response["meta"]["resolved_titles"]
    assert any("Full of Colors 1982" in item for item in response["plan"]["torrent_queries"] + response["plan"]["pan_queries"])
    assert response["results"][0]["match_bucket"] == "weak_context_match"
    text = format_search_text(response, max_results=3)
    assert "No confident match" in text


def test_default_degraded_source_recovers_after_probe_or_real_success(monkeypatch, tmp_path):
    from quarry.sources.base import SourceRuntimeProfile
    
    def fake_profile(name):
        return SourceRuntimeProfile(
            supported_kinds=("general",),
            timeout=10, retries=1, degraded_score_penalty=10, cooldown_seconds=90, failure_threshold=1, query_budget=1,
            default_degraded=(name == "fake_degraded")
        )
    monkeypatch.setattr("quarry.ranking.profile_for", fake_profile)
    monkeypatch.setattr("quarry.engine.profile_for", fake_profile)

    cache = ResourceCache(tmp_path / "cache.db")
    assert source_is_degraded(cache, "fake_degraded") is True

    cache.record_source_status(
        SourceStatus(
            source="fake_degraded",
            channel="pan",
            priority=3,
            ok=True,
            degraded=False,
            failure_kind="probe_ok",
        )
    )
    assert source_is_degraded(cache, "fake_degraded") is False

    cache2 = ResourceCache(tmp_path / "cache2.db")
    cache2.record_source_status(
        SourceStatus(
            source="fake_degraded",
            channel="pan",
            priority=3,
            ok=False,
            degraded=True,
            failure_kind="network",
            error="ssl eof",
        )
    )
    cache2.record_source_status(
        SourceStatus(
            source="fake_degraded",
            channel="pan",
            priority=3,
            ok=True,
            degraded=True,
            failure_kind="",
        )
    )
    cache2.record_source_status(
        SourceStatus(
            source="fake_degraded",
            channel="pan",
            priority=3,
            ok=True,
            degraded=True,
            failure_kind="",
        )
    )
    assert source_is_degraded(cache2, "fake_degraded") is False
