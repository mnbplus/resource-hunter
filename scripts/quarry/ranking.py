from __future__ import annotations

import math
import re
import time
from typing import Any

from .cache import ResourceCache
from .common import (
    extract_season_episode,
    extract_versions,
    extract_year,
    has_chinese,
    normalize_key,
    parse_quality_tags,
    quality_display_from_tags,
    source_priority,
    title_core,
    title_tokens,
    token_overlap_score,
    unique_preserve,
)
from .config import DEFAULT_CONFIG, RankingConfig
from .models import SearchIntent, SearchResult
from .sources import profile_for

ADULT_NOISE_RE = re.compile(
    r"\b(?:xxx|porn|nude|naked|erotic|playboy|brazzers|naughty|"
    r"onlyfans|sexxx|blowjob|hardcore|"
    r"NebraskaCoeds|WowGirls|CharmModels|ThisIsGlamour|Nubiles|"
    r"Tushy|Vixen|Blacked|RealityKings|BangBros)\b",
    re.I,
)


MATCH_BUCKET_ORDER = {
    "exact_title_episode": 0,
    "exact_title_family": 1,
    "title_family_match": 2,
    "episode_only_match": 3,
    "weak_context_match": 4,
}

TIER_ORDER = {"top": 0, "related": 1, "risky": 2}

BUCKET_LABELS = {
    "top": "Top matches",
    "related": "Related matches",
    "risky": "Risky recall",
}


def _target_title_cores(intent: SearchIntent) -> list[str]:
    cores = [
        intent.title_core,
        intent.english_title_core,
        intent.chinese_title_core,
    ]
    # Filter resolved titles: discard single-word English noise from alias resolver
    # (e.g. "Your", "Find", "Keep" extracted from DuckDuckGo snippets)
    for item in intent.resolved_titles:
        core = title_core(item) or item
        tokens = title_tokens(core)
        # Keep if: has Chinese chars, or has >= 2 tokens, or is the primary query core
        if has_chinese(core) or len(tokens) >= 2:
            cores.append(core)
    return unique_preserve([core for core in cores if core])


def _title_signals(intent: SearchIntent, title: str) -> dict[str, Any]:
    candidate_core = title_core(title)
    candidate_tokens = title_tokens(candidate_core or title)
    target_cores = _target_title_cores(intent)
    target_token_sets = [title_tokens(core) for core in target_cores]
    candidate_key = normalize_key(candidate_core or title)
    target_keys = [normalize_key(core) for core in target_cores if normalize_key(core)]
    exact_core_match = bool(candidate_key and candidate_key in target_keys)
    phrase_match = any(
        core
        and (
            candidate_core == core
            or candidate_core.startswith(core + " ")
            or core.startswith(candidate_core + " ")
        )
        for core in target_cores
    )
    starts_with_target = any(tokens and candidate_tokens[: len(tokens)] == tokens for tokens in target_token_sets)
    overlap = max((token_overlap_score(tokens, candidate_tokens) for tokens in target_token_sets), default=0.0)
    # Detect when the full query phrase is embedded inside a longer title
    # (e.g. "kamiina botan" inside "喵萌奶茶屋 lolihouse 上伊那牡丹 kamiina botan yoeru sugata ...")
    # Only count as contained if the phrase starts in the first 40% of the title,
    # so that mention-only titles like "The Writers Room S01E01 Breaking Bad" don't qualify.
    query_contained = False
    if candidate_core:
        for core in target_cores:
            if core and core in candidate_core:
                pos = candidate_core.index(core)
                if pos <= max(len(candidate_core) * 0.4, len(core)):
                    query_contained = True
                    break
    season, episode = extract_season_episode(title)
    season_match = bool(intent.season is None or season == intent.season)
    episode_match = bool(intent.episode is None or episode == intent.episode)
    result_year = extract_year(title)
    year_match = bool(not intent.year or result_year == intent.year or (not result_year and intent.kind in {"tv", "anime"}))
    matched_core = next((core for core in target_cores if normalize_key(core) == candidate_key), target_cores[0] if target_cores else "")
    return {
        "candidate_core": candidate_core,
        "candidate_tokens": candidate_tokens,
        "exact_core_match": exact_core_match,
        "phrase_match": phrase_match,
        "starts_with_target": starts_with_target,
        "query_contained": query_contained,
        "overlap": overlap,
        "season_match": season_match,
        "episode_match": episode_match,
        "result_season": season,
        "result_episode": episode,
        "result_year": result_year,
        "year_match": year_match,
        "matched_core": matched_core,
    }


def classify_result(result: SearchResult, intent: SearchIntent) -> tuple[str, float, list[str], list[str], dict[str, Any]]:
    signals = _title_signals(intent, result.title)
    reasons: list[str] = []
    penalties: list[str] = []

    if signals["exact_core_match"]:
        reasons.append("canonical title match")
    elif signals["phrase_match"]:
        reasons.append("phrase title match")
    elif signals["overlap"] >= 0.9:
        reasons.append("strong title-family match")
    elif signals["overlap"] >= 0.6:
        reasons.append("partial title-family match")
    elif signals["overlap"] > 0:
        reasons.append("weak context match")

    if signals["year_match"] and intent.year:
        reasons.append("year match")
    if intent.season is not None and signals["season_match"]:
        reasons.append("season match")
    if intent.episode is not None and signals["episode_match"]:
        reasons.append("episode match")

    strong_title = signals["exact_core_match"] or signals["phrase_match"] or (signals["overlap"] >= 0.82 and signals["starts_with_target"])
    # query_contained: all query tokens appear as a contiguous phrase inside the title
    # (e.g. searching "Kamiina Botan" and the title contains "... Kamiina Botan ...")
    if signals["query_contained"] and signals["overlap"] >= 0.35:
        strong_title = True
    related_title = strong_title or signals["overlap"] >= 0.62 or signals["query_contained"]

    if intent.kind in {"tv", "anime"} and (intent.season is not None or intent.episode is not None):
        if strong_title and signals["season_match"] and signals["episode_match"]:
            return "exact_title_episode", 0.98, reasons, penalties, signals
        if related_title and signals["season_match"] and signals["episode_match"]:
            return "title_family_match", 0.78, reasons, penalties, signals
        if strong_title and signals["season_match"]:
            penalties.append("missing exact episode evidence")
            return "title_family_match", 0.62, reasons, penalties, signals
        if signals["season_match"] or signals["episode_match"]:
            penalties.append("episode without title-family match")
            return "episode_only_match", 0.26, reasons, penalties, signals
        penalties.append("weak context only")
        return "weak_context_match", 0.08, reasons, penalties, signals

    if strong_title and signals["year_match"]:
        return "exact_title_family", 0.95, reasons, penalties, signals
    if strong_title:
        penalties.append("year not confirmed")
        return "title_family_match", 0.74, reasons, penalties, signals
    if related_title and signals["year_match"]:
        return "title_family_match", 0.64, reasons, penalties, signals
    if signals["overlap"] >= 0.45:
        penalties.append("title-family weak")
        return "title_family_match", 0.48, reasons, penalties, signals
    penalties.append("weak context only")
    return "weak_context_match", 0.12, reasons, penalties, signals


def source_health(cache: ResourceCache | None, source_name: str) -> dict[str, Any]:
    profile = profile_for(source_name)
    if cache is None:
        return {
            "degraded": profile.default_degraded,
            "degraded_reason": "default_degraded" if profile.default_degraded else "",
            "recovery_state": "unknown",
            "last_success_epoch": None,
            "failure_kind": "",
        }
    latest = cache.latest_source_status(source_name)
    if not latest:
        return {
            "degraded": profile.default_degraded,
            "degraded_reason": "default_degraded" if profile.default_degraded else "",
            "recovery_state": "unknown",
            "last_success_epoch": None,
            "failure_kind": "",
        }
    last_success_epoch = latest.get("last_success_epoch")
    if latest.get("skipped") and latest.get("failure_kind") == "circuit_open":
        return {
            "degraded": True,
            "degraded_reason": "circuit_open",
            "recovery_state": "cooldown",
            "last_success_epoch": last_success_epoch,
            "failure_kind": latest.get("failure_kind", ""),
        }
    if profile.default_degraded:
        last_failure = cache.latest_failure_epoch(source_name, within_seconds=900)
        recovery_since = last_failure if last_failure is not None else (time.time() - 900)
        recovered = cache.count_real_successes_since(source_name, recovery_since, within_seconds=900) >= 2
        if latest.get("ok") and latest.get("failure_kind") == "probe_ok":
            recovered = True
        if recovered:
            return {
                "degraded": False,
                "degraded_reason": "",
                "recovery_state": "healthy",
                "last_success_epoch": last_success_epoch,
                "failure_kind": latest.get("failure_kind", ""),
            }
        return {
            "degraded": True,
            "degraded_reason": latest.get("degraded_reason") or latest.get("failure_kind") or "default_degraded",
            "recovery_state": "recovering" if last_success_epoch else "degraded",
            "last_success_epoch": last_success_epoch,
            "failure_kind": latest.get("failure_kind", ""),
        }
    return {
        "degraded": bool(latest.get("degraded")),
        "degraded_reason": latest.get("degraded_reason", ""),
        "recovery_state": latest.get("recovery_state", "healthy" if latest.get("ok") else "degraded"),
        "last_success_epoch": last_success_epoch,
        "failure_kind": latest.get("failure_kind", ""),
    }


def source_is_degraded(cache: ResourceCache | None, source_name: str) -> bool:
    return bool(source_health(cache, source_name).get("degraded"))


def _build_canonical_identity(result: SearchResult, intent: SearchIntent, signals: dict[str, Any]) -> str:
    strong_title = signals.get("exact_core_match") or signals.get("phrase_match") or signals.get("overlap", 0.0) >= 0.82
    base = (
        (signals.get("matched_core") if strong_title else "")
        or signals.get("candidate_core")
        or title_core(result.title)
        or title_core(intent.query)
        or normalize_key(result.title)
    )
    kind = intent.kind
    if kind == "movie":
        year = signals.get("result_year") or intent.resolved_year or intent.year or "na"
        return f"movie:{normalize_key(base)}:{year}"
    if kind in {"tv", "anime"}:
        season = signals.get("result_season") or intent.season or 0
        episode = signals.get("result_episode") or intent.episode or 0
        return f"{kind}:{normalize_key(base)}:s{int(season):02d}e{int(episode):03d}"
    if kind == "software":
        versions = extract_versions(result.title) or intent.version_hints or ["na"]
        return f"software:{normalize_key(base)}:{versions[0]}"
    if kind == "book":
        fmt = result.quality_tags.get("format") or result.quality_tags.get("book_format") or "na"
        return f"book:{normalize_key(base)}:{fmt}"
    if kind == "music":
        quality = "lossless" if result.quality_tags.get("lossless") else "na"
        return f"music:{normalize_key(base)}:{quality}"
    return f"{kind}:{normalize_key(base)}"


def _assign_tier(bucket: str, confidence: float) -> str:
    if bucket in {"exact_title_episode", "exact_title_family"}:
        return "top"
    if bucket == "title_family_match" and confidence >= 0.62:
        return "related"
    if bucket == "title_family_match" and confidence >= 0.48:
        return "related"
    return "risky"


def score_result(result: SearchResult, intent: SearchIntent, cache: ResourceCache | None = None, config: RankingConfig | None = None) -> SearchResult:
    cfg = config or DEFAULT_CONFIG
    bucket, confidence, reasons, penalties, signals = classify_result(result, intent)
    result.match_bucket = bucket
    result.confidence = round(confidence, 3)
    result.reasons = unique_preserve(reasons)
    result.penalties = unique_preserve(penalties)

    tags = result.quality_tags or parse_quality_tags(result.title)
    result.quality_tags = tags
    result.quality = quality_display_from_tags(tags)

    # Adult content filtering — especially important for music searches on general trackers
    if ADULT_NOISE_RE.search(result.title):
        result.penalties.append("adult content filtered")
        result.score = -999
        result.tier = "risky"
        result.match_bucket = "weak_context_match"
        result.confidence = 0.0
        result.canonical_identity = f"adult:{result.share_id_or_info_hash or result.title[:32]}"
        result.evidence = {"filtered": True}
        return result

    score = cfg.bucket_base_score(bucket)

    if signals["exact_core_match"]:
        score += cfg.exact_core_bonus
    if signals["phrase_match"]:
        score += cfg.phrase_match_bonus
    score += int(signals["overlap"] * cfg.overlap_multiplier)
    if signals["year_match"] and intent.year:
        score += cfg.year_match_bonus
    if intent.season is not None and signals["season_match"]:
        score += cfg.season_match_bonus
    if intent.episode is not None and signals["episode_match"]:
        score += cfg.episode_match_bonus

    resolution = tags.get("resolution")
    if resolution == "2160p":
        score += cfg.resolution_4k_bonus
        result.reasons.append("4k resolution")
    elif resolution == "1080p":
        score += cfg.resolution_1080p_bonus
        result.reasons.append("1080p resolution")
    elif resolution == "720p":
        score += cfg.resolution_720p_bonus
        result.reasons.append("720p resolution")

    source_type = tags.get("source_type") or tags.get("source")
    if source_type == "bluray":
        score += cfg.bluray_source_bonus
        result.reasons.append("bluray source")
    elif source_type == "web-dl":
        score += cfg.webdl_source_bonus
        result.reasons.append("web-dl source")
    elif source_type in {"webrip", "hdtv"}:
        score += cfg.webrip_hdtv_bonus
        result.reasons.append(f"{source_type} source")
    elif source_type == "cam":
        score += cfg.cam_penalty
        result.penalties.append("cam-quality release")
    if tags.get("pack") == "remux":
        score += cfg.remux_bonus
        result.reasons.append("remux pack")
    if tags.get("hdr_flags"):
        score += min(cfg.hdr_max_bonus, cfg.hdr_per_flag_bonus * len(tags["hdr_flags"]))
        result.reasons.append("hdr flags")
    preference_mismatch = False

    if intent.wants_sub and tags.get("subtitle"):
        score += cfg.subtitle_bonus
        result.reasons.append("subtitle requested")
    if intent.wants_4k and resolution == "2160p":
        score += cfg.wants_4k_bonus
        result.reasons.append("4k requested")
    if intent.kind == "music" and tags.get("lossless"):
        score += cfg.lossless_bonus
        result.reasons.append("lossless audio")
    if intent.kind == "music" and tags.get("hires"):
        score += cfg.hires_bonus
        result.reasons.append("hi-res audio")
    if intent.kind == "music":
        music_source = tags.get("music_source", "")
        if music_source == "web-hires":
            score += cfg.music_hires_source_bonus
            result.reasons.append(f"hi-res source ({music_source})")
        elif music_source == "cd":
            score += cfg.music_cd_source_bonus
            result.reasons.append("CD source")
        # Penalize lossy when user wants lossless
        lowered_query = intent.original_query.lower()
        wants_lossless = any(term in lowered_query for term in ("flac", "lossless")) or "\u65e0\u635f" in intent.original_query
        if wants_lossless and not tags.get("lossless"):
            preference_mismatch = True
            score += cfg.lossless_mismatch_penalty
            result.penalties.append("lossless preference mismatch")
        # Penalize lossy formats (mp3/aac) for music searches
        if tags.get("lossy") and not tags.get("lossless"):
            score += cfg.music_lossy_penalty
            result.penalties.append("lossy audio format")
        # Flag unverified lossless (FLAC but no known source, no bit depth info)
        if tags.get("lossless") and not tags.get("hires") and not music_source and not tags.get("bit_depth"):
            result.penalties.append("lossless source unverified")
    if intent.kind == "book" and tags.get("format"):
        score += cfg.book_format_bonus
        result.reasons.append("book format match")
    if intent.kind == "book" and intent.format_hints:
        if tags.get("format") in intent.format_hints or tags.get("book_format") in intent.format_hints:
            score += cfg.book_format_match_bonus
            result.reasons.append("requested format match")
        else:
            preference_mismatch = True
            score += cfg.book_format_mismatch_penalty
            result.penalties.append("requested format mismatch")
    if intent.kind == "software":
        lowered_query = intent.original_query.lower()
        platform_hint = next((hint for hint in ("windows", "mac", "linux") if hint in lowered_query), "")
        if platform_hint:
            if platform_hint in result.title.lower():
                score += cfg.platform_hint_match_bonus
                result.reasons.append("platform hint match")
            else:
                preference_mismatch = True
                score += cfg.platform_hint_mismatch_penalty
                result.penalties.append("platform hint mismatch")

    if result.channel == "pan":
        score += cfg.pan_provider_score(result.provider)
        if result.password:
            score += cfg.pan_password_bonus
            result.reasons.append("has extraction code")
    if result.channel == "torrent" and result.seeders:
        # Log-based seeder scoring: 10→10pts, 100→20pts, 1000→30pts, 10000→40pts
        seeder_score = int(math.log10(max(result.seeders, 1)) * 10)
        score += min(seeder_score, cfg.seeder_cap // cfg.seeder_divisor)
        result.reasons.append(f"seeders ({result.seeders})")

    score += max(0, 12 - source_priority(result.source))
    result.reasons.append(f"source priority {source_priority(result.source)}")

    health = source_health(cache, result.source)
    result.source_health = health
    result.source_degraded = bool(health.get("degraded"))
    if result.source_degraded:
        penalty = profile_for(result.source).degraded_score_penalty
        if penalty:
            score -= penalty
            result.penalties.append(f"degraded source penalty ({penalty})")

    if bucket == "episode_only_match":
        score += cfg.episode_only_penalty
        result.penalties.append("episode-only match penalty")
    elif bucket == "weak_context_match":
        score += cfg.weak_context_penalty
        result.penalties.append("weak-context penalty")

    result.canonical_identity = _build_canonical_identity(result, intent, signals)
    result.evidence = {
        "title_core": signals["candidate_core"],
        "matched_core": signals["matched_core"],
        "exact_core_match": signals["exact_core_match"],
        "phrase_match": signals["phrase_match"],
        "starts_with_target": signals["starts_with_target"],
        "overlap": signals["overlap"],
        "year_match": signals["year_match"],
        "season_match": signals["season_match"],
        "episode_match": signals["episode_match"],
        "result_year": signals["result_year"],
        "result_season": signals["result_season"],
        "result_episode": signals["result_episode"],
        "preference_mismatch": preference_mismatch,
    }
    result.tier = "related" if preference_mismatch and _assign_tier(bucket, result.confidence) == "top" else _assign_tier(bucket, result.confidence)
    result.score = score
    result.reasons = unique_preserve(result.reasons)
    result.penalties = unique_preserve(result.penalties)
    return result


def _choice_tuple(result: SearchResult) -> tuple[int, int, int, int, int, int, int]:
    return (
        -MATCH_BUCKET_ORDER.get(result.match_bucket, 99),
        -TIER_ORDER.get(result.tier, 99),
        result.score,
        0 if not result.source_degraded else -1,
        result.seeders,
        1 if result.password else 0,
        len(result.title),
    )


def deduplicate_results(results: list[SearchResult]) -> list[SearchResult]:
    chosen: dict[str, SearchResult] = {}
    for result in results:
        key = result.canonical_identity or result.share_id_or_info_hash or normalize_key(result.title)[:96]
        current = chosen.get(key)
        if not current or _choice_tuple(result) > _choice_tuple(current):
            chosen[key] = result
    return list(chosen.values())


def sort_results(results: list[SearchResult]) -> list[SearchResult]:
    return sorted(
        results,
        key=lambda item: (
            TIER_ORDER.get(item.tier, 99),
            MATCH_BUCKET_ORDER.get(item.match_bucket, 99),
            -item.score,
            item.source_degraded,
            -item.seeders,
            item.title.lower(),
        ),
    )


def diversify_results(results: list[SearchResult], head_size: int = 8) -> list[SearchResult]:
    sorted_all = sort_results(results)
    if len(sorted_all) <= head_size:
        # Small result set — no diversity needed, just sort
        return sorted_all

    # Apply diversity penalties only to the first `head_size` picks
    remaining = list(sorted_all)
    selected: list[SearchResult] = []
    source_counts: dict[str, int] = {}
    provider_counts: dict[str, int] = {}
    quality_counts: dict[str, int] = {}
    while remaining and len(selected) < head_size:
        best_index = 0
        best_value: tuple[float, int] | None = None
        for index, item in enumerate(remaining):
            adjusted = float(item.score)
            adjusted -= source_counts.get(item.source, 0) * 12
            adjusted -= provider_counts.get(item.provider, 0) * 6
            adjusted -= quality_counts.get(item.quality or "na", 0) * 4
            value = (adjusted, -index)
            if best_value is None or value > best_value:
                best_value = value
                best_index = index
        chosen = remaining.pop(best_index)
        selected.append(chosen)
        source_counts[chosen.source] = source_counts.get(chosen.source, 0) + 1
        provider_counts[chosen.provider] = provider_counts.get(chosen.provider, 0) + 1
        quality_counts[chosen.quality or "na"] = quality_counts.get(chosen.quality or "na", 0) + 1

    # Append tail in pre-sorted order (no diversity penalty — already ranked)
    selected.extend(remaining)
    return selected


__all__ = [
    "BUCKET_LABELS",
    "MATCH_BUCKET_ORDER",
    "classify_result",
    "deduplicate_results",
    "diversify_results",
    "score_result",
    "sort_results",
    "source_health",
    "source_is_degraded",
]
