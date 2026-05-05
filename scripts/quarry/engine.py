from __future__ import annotations

import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from .pan_probe import PanLinkProber

from .benchmark import run_benchmark_suite
from .cache import ResourceCache
from .config import DEFAULT_CONFIG, RankingConfig
from .exceptions import SourceError, SourceNetworkError, SourceParseError, SourceRateLimitError, SourceUnavailableError
from .intent import AliasResolver, build_plan, enrich_intent_with_aliases
from .models import SearchIntent, SearchPlan, SearchResult, SourceStatus
from .ranking import deduplicate_results, diversify_results, score_result, source_health, source_is_degraded
from .sources import HTTPClient, SourceAdapter, default_adapters, profile_for


def _load_local_config() -> RankingConfig:
    """Load user ranking overrides from ``local/config.json`` if it exists."""
    local_config = Path(__file__).resolve().parent.parent.parent / "local" / "config.json"
    if local_config.is_file():
        try:
            return RankingConfig.from_file(local_config)
        except Exception:
            pass  # broken config → fall back to defaults
    return DEFAULT_CONFIG


def _classify_failure_kind(error: str | Exception) -> str:
    """Classify a failure for health tracking.

    Supports both structured SourceError subtypes and legacy string matching.
    """
    # Structured exception classification (preferred)
    if isinstance(error, SourceRateLimitError):
        return "rate_limit"
    if isinstance(error, SourceUnavailableError):
        return "http_5xx"
    if isinstance(error, SourceNetworkError):
        return "network"
    if isinstance(error, SourceParseError):
        return "parse"
    if isinstance(error, SourceError):
        return "source_error"
    # Legacy string matching for sources that still throw RuntimeError
    lowered = (str(error) if error else "").lower()
    if lowered.startswith("http 4"):
        return "http_4xx"
    if lowered.startswith("http 5"):
        return "http_5xx"
    if "invalid json" in lowered:
        return "json"
    if "unexpected pan payload shape" in lowered or "unexpected payload type" in lowered:
        return "schema"
    if "ssl" in lowered or "timed out" in lowered or "urlopen error" in lowered:
        return "network"
    if "circuit open" in lowered:
        return "circuit_open"
    return "unknown"


class ResourceHunterEngine:
    def __init__(self, cache: ResourceCache | None = None, http_client: HTTPClient | None = None) -> None:
        self.cache = cache or ResourceCache()
        self.http_client = http_client or HTTPClient(retries=1, default_timeout=10)
        self.alias_resolver = AliasResolver()
        self.pan_sources, self.torrent_sources = default_adapters()
        self.pan_prober = PanLinkProber()
        self.config = _load_local_config()
        try:
            self.cache.cleanup()
        except Exception:
            pass

    def _resolve_aliases(self, intent: SearchIntent) -> SearchIntent:
        alias_resolution = self.alias_resolver.resolve(intent, self.cache, self.http_client)
        return enrich_intent_with_aliases(intent, alias_resolution)

    def _cache_key(self, intent: SearchIntent, plan: SearchPlan, page: int, limit: int, probe_links: bool = True) -> str:
        payload = json.dumps(
            {
                "schema_version": "3",
                "intent": intent.to_dict(),
                "plan": plan.to_dict(),
                "page": page,
                "limit": limit,
                "probe_links": probe_links,
            },
            sort_keys=True,
            ensure_ascii=False,
        )
        return str(__import__("hashlib").sha256(payload.encode("utf-8")).hexdigest())

    def _catalog_for_channel(self, channel: str) -> list[SourceAdapter]:
        return self.pan_sources if channel == "pan" else self.torrent_sources

    def _ordered_sources(self, channel: str, plan: SearchPlan, intent: SearchIntent) -> list[SourceAdapter]:
        preferred_names = plan.preferred_pan_sources if channel == "pan" else plan.preferred_torrent_sources
        preferred = {name: index for index, name in enumerate(preferred_names)}
        catalog = [
            source
            for source in self._catalog_for_channel(channel)
            if source.supports(intent)
        ]
        return sorted(
            catalog,
            key=lambda item: (
                preferred.get(item.name, 999) + (100 if source_is_degraded(self.cache, item.name) else 0),
                item.priority,
            ),
        )

    def _search_source(
        self,
        source: SourceAdapter,
        channel: str,
        queries: list[str],
        intent: SearchIntent,
        page: int,
        limit: int,
    ) -> tuple[SourceStatus, list[SearchResult]]:
        profile = profile_for(source.name)
        current_health = source_health(self.cache, source.name)
        degraded_before = bool(current_health.get("degraded"))
        if self.cache.should_skip_source(source.name, profile.cooldown_seconds, profile.failure_threshold):
            status = SourceStatus(
                source=source.name,
                channel=channel,
                priority=source.priority,
                ok=False,
                skipped=True,
                degraded=True,
                degraded_reason="circuit_open",
                recovery_state="cooldown",
                last_success_epoch=current_health.get("last_success_epoch"),
                error="circuit open from recent failures",
                failure_kind="circuit_open",
            )
            self.cache.record_source_status(status)
            return status, []

        status = SourceStatus(
            source=source.name,
            channel=channel,
            priority=source.priority,
            ok=True,
            skipped=False,
            degraded=degraded_before,
            degraded_reason=current_health.get("degraded_reason", ""),
            recovery_state=current_health.get("recovery_state", "healthy"),
            last_success_epoch=current_health.get("last_success_epoch"),
        )
        results: list[SearchResult] = []
        client = HTTPClient(retries=profile.retries, default_timeout=profile.timeout)
        query_budget = 1 if (profile.default_degraded or degraded_before) else profile.query_budget
        try:
            for query in queries[:query_budget]:
                if not query:
                    continue
                started = time.time()
                try:
                    batch = source.search(query, intent, limit, page, client)
                    status.latency_ms = int((time.time() - started) * 1000)
                    status.ok = True
                    status.error = ""
                    status.failure_kind = ""
                    if batch:
                        status.degraded = source_health(self.cache, source.name).get("degraded", degraded_before)
                        status.degraded_reason = current_health.get("degraded_reason", "")
                        status.recovery_state = "healthy" if not status.degraded else "recovering"
                        results.extend(batch)
                        break
                except Exception as exc:
                    status.ok = False
                    status.latency_ms = int((time.time() - started) * 1000)
                    status.error = str(exc)[:200]
                    status.failure_kind = _classify_failure_kind(exc)
                    status.degraded = profile.default_degraded or degraded_before
                    status.degraded_reason = status.failure_kind or "request_failure"
                    status.recovery_state = "recovering" if status.degraded else "degraded"
        finally:
            client.close()
        self.cache.record_source_status(status)
        return status, results

    def _probe_pan_results(self, results: list[SearchResult]) -> list[SearchResult]:
        """Probe pan links for viability and demote dead links to 'risky'."""
        pan_candidates = [
            (i, r) for i, r in enumerate(results)
            if r.channel == "pan" and r.tier in ("top", "related")
        ]
        if not pan_candidates:
            return results
        probe_items = [(r.link_or_magnet, r.provider) for _, r in pan_candidates]
        probe_results = self.pan_prober.probe_batch(probe_items, max_workers=4)
        for (idx, result), probe in zip(pan_candidates, probe_results):
            result.source_health["link_alive"] = probe.alive
            result.source_health["link_probe_reason"] = probe.reason
            if probe.title:
                result.source_health["link_title"] = probe.title
            if probe.alive is False:
                result.tier = "risky"
                result.penalties.append("dead link detected")
                result.score -= 50
        return results

    def search(
        self,
        intent: SearchIntent,
        plan: SearchPlan | None = None,
        page: int = 1,
        limit: int = 8,
        use_cache: bool = True,
        probe_links: bool = True,
        max_sources: int = 0,
    ) -> dict[str, Any]:
        intent = self._resolve_aliases(intent)
        plan = plan or build_plan(intent)
        cache_key = self._cache_key(intent, plan, page, limit, probe_links=probe_links)
        if use_cache:
            cached = self.cache.get_search_cache(cache_key)
            if cached:
                cached.setdefault("meta", {})
                cached["meta"]["cached"] = True
                return cached

        results: list[SearchResult] = []
        statuses: list[SourceStatus] = []
        warnings: list[str] = []

        all_futures: list[tuple[str, Any]] = []
        max_workers = min(8, max(4, sum(len(self._ordered_sources(ch, plan, intent)) for ch in plan.channels)))
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            for channel in plan.channels:
                queries = plan.pan_queries if channel == "pan" else plan.torrent_queries
                ordered_sources = self._ordered_sources(channel, plan, intent)
                if max_sources > 0:
                    ordered_sources = ordered_sources[:max_sources]
                for source in ordered_sources:
                    future = executor.submit(self._search_source, source, channel, queries, intent, page, limit)
                    all_futures.append((channel, future))
            for channel, future in all_futures:
                try:
                    status, source_results = future.result()
                    statuses.append(status)
                    results.extend(source_results)
                except Exception as exc:
                    import logging
                    logging.getLogger(__name__).warning("source future failed: %s", exc)

        scored = [score_result(result, intent, cache=self.cache) for result in results]
        deduped = deduplicate_results(scored)
        if probe_links:
            deduped = self._probe_pan_results(deduped)
        ordered = diversify_results(deduped)
        suppressed = [
            {
                "title": item.title,
                "source": item.source,
                "tier": item.tier,
                "reason": item.match_bucket,
                "score": item.score,
            }
            for item in ordered
            if item.tier == "risky"
        ]
        statuses.sort(key=lambda item: (item.channel, item.priority, item.source))

        if not ordered:
            warnings.append("no results returned from active sources")

        # Zero-config diagnostics: help users understand what can be enabled
        warnings.extend(self._zero_config_warnings(statuses, ordered))

        retry_info: dict[str, Any] = {}

        # Smart auto-retry: if zero results, simplify the query and retry once
        if not ordered and page == 1:
            simplified = _simplify_query(intent.original_query)
            if simplified and simplified != intent.original_query:
                from .intent import parse_intent
                retry_intent = parse_intent(simplified, explicit_kind=intent.kind)
                retry_intent = self._resolve_aliases(retry_intent)
                retry_plan = build_plan(retry_intent)
                retry_response = self.search(
                    retry_intent, retry_plan, page=1, limit=limit,
                    use_cache=use_cache, probe_links=probe_links,
                )
                if retry_response.get("results"):
                    retry_response.setdefault("meta", {})
                    retry_response["meta"]["auto_retry"] = True
                    retry_response["meta"]["original_query"] = intent.original_query
                    retry_response["meta"]["retry_query"] = simplified
                    retry_response["warnings"] = (
                        [f"auto-retry: simplified \"{intent.original_query}\" → \"{simplified}\""]
                        + retry_response.get("warnings", [])
                    )
                    return retry_response
                retry_info = {"attempted": simplified, "success": False}

        response = {
            "schema_version": "3",
            "query": intent.original_query,
            "summary": _build_summary(intent, ordered, warnings),
            "intent": intent.to_dict(),
            "plan": plan.to_dict(),
            "results": [result.to_public_dict() for result in ordered],
            "suppressed": suppressed,
            "warnings": warnings,
            "source_status": [status.to_dict() for status in statuses],
            "meta": {
                "cached": False,
                "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "limit": limit,
                "page": page,
                "candidate_count": len(ordered),
                "effective_limit": min(limit, 4) if intent.quick else limit,
                "alias_resolution": intent.alias_resolution,
                "resolved_titles": intent.resolved_titles,
                "resolved_year": intent.resolved_year or intent.year,
                "retry_info": retry_info if retry_info else None,
                "fast_mode": max_sources > 0,
            },
        }
        # Record search in persistent history
        try:
            top_source = ordered[0].source if ordered else ""
            self.cache.record_search(
                query=intent.original_query,
                kind=intent.kind,
                channel=",".join(plan.channels),
                result_count=len(ordered),
                top_source=top_source,
            )
        except Exception:
            pass  # never block search on history recording
        if use_cache:
            self.cache.set_search_cache(cache_key, response, ttl_seconds=300)
        return response

    def source_catalog(self, probe: bool = False) -> dict[str, Any]:
        sources: list[dict[str, Any]] = []
        cached_status = {row["source"]: row for row in self.cache.list_source_statuses()}
        all_sources = self.pan_sources + self.torrent_sources
        for adapter in sorted(all_sources, key=lambda item: (item.channel, item.priority, item.name)):
            status_info = cached_status.get(adapter.name, {})
            if probe:
                profile = profile_for(adapter.name)
                started = time.time()
                ok, error = adapter.healthcheck(HTTPClient(retries=profile.retries, default_timeout=profile.timeout))
                status = SourceStatus(
                    source=adapter.name,
                    channel=adapter.channel,
                    priority=adapter.priority,
                    ok=ok,
                    skipped=False,
                    degraded=False if ok and not profile.default_degraded else profile.default_degraded and not ok,
                    degraded_reason="" if ok else (_classify_failure_kind(error) or "probe_failure"),
                    recovery_state="healthy" if ok else "degraded",
                    last_success_epoch=self.cache.latest_success_epoch(adapter.name),
                    error=error,
                    failure_kind="probe_ok" if ok else _classify_failure_kind(error),
                    latency_ms=int((time.time() - started) * 1000),
                )
                self.cache.record_source_status(status)
                status_info = status.to_dict()
            if status_info:
                latest_health = {
                    "degraded": bool(status_info.get("degraded")),
                    "degraded_reason": status_info.get("degraded_reason", ""),
                    "recovery_state": status_info.get("recovery_state", "unknown"),
                    "last_success_epoch": status_info.get("last_success_epoch"),
                    "failure_kind": status_info.get("failure_kind", ""),
                }
            else:
                latest_health = source_health(self.cache, adapter.name)
            sources.append(
                {
                    "source": adapter.name,
                    "channel": adapter.channel,
                    "priority": adapter.priority,
                    "capability": adapter.capability_profile(),
                    "recent_status": {
                        "ok": bool(status_info.get("ok")) if status_info else None,
                        "skipped": bool(status_info.get("skipped")) if status_info else False,
                        "degraded": latest_health["degraded"],
                        "degraded_reason": latest_health.get("degraded_reason", ""),
                        "recovery_state": latest_health.get("recovery_state", "unknown"),
                        "last_success_epoch": latest_health.get("last_success_epoch"),
                        "latency_ms": status_info.get("latency_ms"),
                        "error": status_info.get("error", ""),
                        "failure_kind": latest_health.get("failure_kind", ""),
                        "checked_at": status_info.get("checked_at"),
                    },
                }
            )
        return {"schema_version": "3", "sources": sources, "meta": {"probe": probe}}

    def run_benchmark(self) -> dict[str, Any]:
        return run_benchmark_suite()

    # -- zero-config intelligence -----------------------------------------

    @staticmethod
    def _zero_config_warnings(
        statuses: list[SourceStatus],
        results: list[SearchResult],
    ) -> list[str]:
        """Generate helpful diagnostics for zero-config users.

        Non-blocking — these appear in the warnings array only when
        optional sources are unconfigured and could improve coverage.
        """
        warnings: list[str] = []

        # Count active pan results
        pan_results = [r for r in results if r.channel == "pan"]
        pan_statuses = [s for s in statuses if s.channel == "pan"]
        active_pan = [s for s in pan_statuses if s.ok and not s.skipped]

        # If no pan results and optional pan tokens are missing
        if not pan_results:
            missing: list[str] = []
            if not os.environ.get("PANSOU_TOKEN", "").strip():
                missing.append("PANSOU_TOKEN (enables ps.252035 + panhunt)")
            if not os.environ.get("PANSOU_API_URL", "").strip():
                missing.append("PANSOU_API_URL (enables self-hosted pansou)")
            if missing and len(active_pan) <= 1:
                warnings.append(
                    f"low pan coverage — configure {', '.join(missing)} in .env for more cloud drive results"
                )

        # If torznab is not configured, mention it as an enhancement option
        if not os.environ.get("TORZNAB_URL", "").strip():
            torrent_results = [r for r in results if r.channel == "torrent"]
            if len(torrent_results) < 3:
                warnings.append(
                    "tip: TORZNAB_URL + TORZNAB_APIKEY (Jackett/Prowlarr) unlocks 500+ additional trackers"
                )

        return warnings


def _build_summary(intent: SearchIntent, results: list[SearchResult], warnings: list[str]) -> str:
    """Generate a natural-language summary of search results for AI agents.

    This lets agents present results to users without deeply parsing the JSON.
    """
    if not results:
        parts = [f'No results found for "{intent.original_query}"']
        if intent.kind not in ("general",):
            parts.append(f" (category: {intent.kind})")
        parts.append(". Try alternative English titles or simplify the query.")
        return "".join(parts)

    top_results = [r for r in results if r.tier == "top"]
    related_results = [r for r in results if r.tier == "related"]
    total_confident = len(top_results) + len(related_results)

    if not total_confident:
        return f'Found {len(results)} results for "{intent.original_query}" but none with high confidence. Results may be unreliable.'

    # Build quality description
    quality_set: set[str] = set()
    source_set: set[str] = set()
    provider_set: set[str] = set()
    best = top_results[0] if top_results else related_results[0]
    for r in (top_results + related_results)[:8]:
        if r.quality:
            quality_set.add(r.quality.split()[0])  # e.g. "2160p" from "2160p BluRay REMUX"
        source_set.add(r.source)
        if r.provider:
            provider_set.add(r.provider)

    parts = [f'Found {total_confident} confident result{"s" if total_confident != 1 else ""}']
    if top_results:
        parts.append(f" ({len(top_results)} top-tier)")
    parts.append(f' for "{intent.original_query}"')

    if quality_set:
        parts.append(f". Available quality: {', '.join(sorted(quality_set, reverse=True))}")
    parts.append(f". Best match: \"{best.title}\" via {best.source}")
    if best.channel == "pan" and best.provider:
        parts.append(f" ({best.provider})")
    if best.seeders and best.channel == "torrent":
        parts.append(f" ({best.seeders} seeders)")

    # Alive/dead link info for pan
    alive_count = sum(1 for r in top_results + related_results if r.source_health.get("link_alive") is True)
    dead_count = sum(1 for r in top_results + related_results if r.source_health.get("link_alive") is False)
    if alive_count:
        parts.append(f". {alive_count} link{'s' if alive_count != 1 else ''} verified alive")
    if dead_count:
        parts.append(f", {dead_count} dead link{'s' if dead_count != 1 else ''} filtered")

    parts.append(".")
    return "".join(parts)



def _simplify_query(query: str) -> str:
    """Progressively simplify a query for auto-retry.

    Strategy: strip quality/format modifiers, keep the core title.
    Example: "Oppenheimer 2023 4K BluRay REMUX" → "Oppenheimer 2023"
    """
    # Strip common quality/format suffixes
    simplified = re.sub(
        r'\b(?:4[Kk]|2160[Pp]|1080[Pp]|720[Pp]|480[Pp]|UHD|HDR10?\+?|DV|'
        r'[Bb]lu-?[Rr]ay|REMUX|WEB-?DL|WEB-?Rip|HDCAM|DVDRip|BDRip|'
        r'HEVC|H\.?265|H\.?264|x264|x265|AAC|DTS(?:-HD)?|Atmos|'
        r'FLAC|MP3|WAV|ALAC|epub|pdf|mobi|azw3?|djvu)\b',
        '', query, flags=re.I
    )
    # Clean up extra whitespace
    simplified = re.sub(r'\s+', ' ', simplified).strip()
    # Don't retry if nothing was stripped or result is too short
    if simplified == query or len(simplified) < 3:
        return ""
    return simplified


__all__ = ["ResourceHunterEngine", "build_plan", "source_health", "source_is_degraded"]
