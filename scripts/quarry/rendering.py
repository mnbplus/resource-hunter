from __future__ import annotations

from typing import Any

from .common import dump_json
from .ranking import BUCKET_LABELS


def _display_tier(item: dict[str, Any]) -> str:
    if item.get("tier"):
        return str(item["tier"])
    bucket = item.get("match_bucket")
    if bucket in {"exact_title_episode", "exact_title_family"}:
        return "top"
    if bucket == "title_family_match":
        return "related"
    return "risky"


def format_search_text(response: dict[str, Any], max_results: int | None = None) -> str:
    intent = response["intent"]
    plan = response["plan"]
    results = response["results"]
    meta = response.get("meta", {})
    limit = max_results if max_results is not None else meta.get("effective_limit", meta.get("limit", 8))
    visible = [item for item in results if _display_tier(item) in {"top", "related"}][:limit]

    lines = [
        f"Quarry v{response.get('schema_version', '3')}",
        f"Query: {response['query']}",
        f"Kind: {intent['kind']} | Channel: {' -> '.join(plan['channels'])}",
    ]
    if plan.get("notes"):
        lines.append("Plan: " + "; ".join(plan["notes"][:4]))
    if meta.get("resolved_titles"):
        lines.append("Resolved titles: " + ", ".join(meta["resolved_titles"][:4]))
    lines.append("")

    if not visible:
        lines.append("No confident match")
        lines.append("")
    else:
        grouped: dict[str, list[dict[str, Any]]] = {"top": [], "related": []}
        for result in visible:
            grouped.setdefault(_display_tier(result), []).append(result)
        for tier in ("top", "related"):
            bucket_items = grouped.get(tier) or []
            if not bucket_items:
                continue
            lines.append(BUCKET_LABELS[tier] + ":")
            for result in bucket_items:
                summary_bits = [
                    f"{result['channel']}/{result['provider']}",
                    f"via {result['source']}",
                    f"tier={_display_tier(result)}",
                    f"confidence={result.get('confidence')}",
                ]
                if result.get("quality"):
                    summary_bits.append(result["quality"])
                if result.get("size"):
                    summary_bits.append(result["size"])
                if result.get("seeders"):
                    summary_bits.append(f"seeders={result['seeders']}")
                summary_bits.append(f"score={result['score']}")
                if result.get("source_degraded"):
                    summary_bits.append("degraded-source")
                lines.append(f"- {result['title']}")
                lines.append("  " + " | ".join(summary_bits))
                lines.append(f"  {result['link_or_magnet']}")
                if result.get("password"):
                    lines.append(f"  password: {result['password']}")
                why = ", ".join(result.get("reasons", [])[:4])
                if why:
                    lines.append(f"  why: {why}")
                penalty = ", ".join(result.get("penalties", [])[:3])
                if penalty:
                    lines.append(f"  penalties: {penalty}")
                health = result.get("source_health") or {}
                if health.get("degraded"):
                    lines.append(
                        f"  health: {health.get('recovery_state', 'degraded')} ({health.get('degraded_reason') or health.get('failure_kind') or 'degraded'})"
                    )
            lines.append("")

    if response.get("warnings"):
        lines.append("Warnings:")
        for warning in response["warnings"]:
            lines.append(f"- {warning}")
    if response.get("source_status"):
        lines.append("")
        lines.append("Source status:")
        for status in response["source_status"]:
            state = "ok" if status["ok"] else ("skipped" if status["skipped"] else "fail")
            if status.get("degraded"):
                state += "/degraded"
            detail = f"{status['source']} ({status['channel']}, p{status['priority']}): {state}"
            if status.get("latency_ms") is not None:
                detail += f", {status['latency_ms']}ms"
            if status.get("failure_kind"):
                detail += f", {status['failure_kind']}"
            if status.get("recovery_state"):
                detail += f", recovery={status['recovery_state']}"
            if status.get("error"):
                detail += f", {status['error']}"
            lines.append(f"- {detail}")
    return "\n".join(lines).strip()


def format_sources_text(payload: dict[str, Any]) -> str:
    lines = ["Quarry sources", ""]
    for item in payload["sources"]:
        status = item["recent_status"]
        state = "unknown"
        if status["ok"] is True:
            state = "ok"
        elif status["ok"] is False and status["skipped"]:
            state = "skipped"
        elif status["ok"] is False:
            state = "fail"
        if status.get("degraded"):
            state += "/degraded"
        lines.append(f"- {item['source']} | {item['channel']} | priority={item['priority']} | status={state}")
        capability = item.get("capability") or {}
        if capability:
            lines.append(
                "  capability="
                + ",".join(capability.get("supported_kinds", []))
                + f" query_budget={capability.get('query_budget')} timeout={capability.get('timeout')}"
            )
        if status.get("latency_ms") is not None or status.get("checked_at"):
            lines.append(
                f"  checked_at={status.get('checked_at') or '-'} latency_ms={status.get('latency_ms') or '-'} recovery={status.get('recovery_state') or '-'}"
            )
        if status.get("degraded_reason"):
            lines.append(f"  degraded_reason={status['degraded_reason']}")
        if status.get("failure_kind"):
            lines.append(f"  failure_kind={status['failure_kind']}")
        if status.get("error"):
            lines.append(f"  error={status['error']}")
    return "\n".join(lines)


def format_benchmark_text(payload: dict[str, Any]) -> str:
    lines = [
        "Quarry benchmark",
        f"Search cases: {payload['search_cases']} | Video cases: {payload['video_cases']}",
        f"Pass: {payload['pass']}",
        f"Overall Top1: {payload['overall']['top1_rate']:.1%}",
        f"Overall Top3: {payload['overall']['top3_rate']:.1%}",
        f"High-confidence error rate: {payload['overall']['high_conf_error_rate']:.1%}",
        "",
        "Per kind:",
    ]
    for kind, stats in payload["by_kind"].items():
        lines.append(
            f"- {kind}: top1={stats['top1_rate']:.1%} top3={stats['top3_rate']:.1%} cases={stats['cases']}"
        )
    lines.append("")
    lines.append("Adversarial:")
    lines.append(
        f"- cases={payload['adversarial']['cases']} top_failures={payload['adversarial']['top_failures']}"
    )
    lines.append("")
    lines.append("Thresholds:")
    for name, passed in payload["thresholds"].items():
        lines.append(f"- {name}: {'PASS' if passed else 'FAIL'}")
    return "\n".join(lines)


def search_response_to_v2(response: dict[str, Any]) -> dict[str, Any]:
    """Convert a v3 response to v2 format for backward compatibility.

    .. deprecated:: 1.1.0
        No known callers. Will be removed in a future version.
    """
    import warnings
    warnings.warn("search_response_to_v2 is deprecated and will be removed in v1.5.0", DeprecationWarning, stacklevel=2)
    return {
        "query": response["query"],
        "intent": response["intent"],
        "plan": response["plan"],
        "results": [
            {
                "channel": item["channel"],
                "source": item["source"],
                "provider": item["provider"],
                "title": item["title"],
                "link_or_magnet": item["link_or_magnet"],
                "password": item["password"],
                "share_id_or_info_hash": item["share_id_or_info_hash"],
                "size": item["size"],
                "seeders": item["seeders"],
                "quality": item["quality"],
                "score": item["score"],
                "reasons": item["reasons"],
                "penalties": item.get("penalties", []),
                "match_bucket": item.get("match_bucket"),
                "confidence": item.get("confidence"),
                "source_degraded": item.get("source_degraded"),
                "raw": item.get("raw", {}),
            }
            for item in response["results"]
        ],
        "warnings": response.get("warnings", []),
        "source_status": response.get("source_status", []),
        "meta": {
            **response.get("meta", {}),
            "cached": response.get("meta", {}).get("cached", False),
        },
    }


def maybe_dump_json(payload: dict[str, Any], as_json: bool) -> str:
    return dump_json(payload) if as_json else ""
