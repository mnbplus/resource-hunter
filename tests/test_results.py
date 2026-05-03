from quarry.intent import parse_intent
from quarry.ranking import deduplicate_results, score_result
from quarry.models import SearchResult


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
