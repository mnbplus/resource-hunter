from __future__ import annotations

import argparse
import os
import sys
from typing import Any

from .cache import ResourceCache
from .common import dump_json, ensure_utf8_stdio, storage_root
from .engine import ResourceHunterEngine
from .intent import build_plan, parse_intent
from .rendering import format_benchmark_text, format_search_text, format_sources_text, search_response_to_v2
from .subdl import SubDLClient, format_subtitle_results
from .subhd import SubHDClient
from .jimaku import JimakuClient
from .video_core import VideoManager, format_video_text


def _resolve_kind(args: argparse.Namespace) -> str | None:
    if getattr(args, "kind", None):
        return str(args.kind)
    for name in ("movie", "tv", "anime", "music", "software", "book", "general"):
        if getattr(args, name, False):
            return name
    return None


def _resolve_channel(args: argparse.Namespace) -> str:
    if getattr(args, "pan_only", False):
        return "pan"
    if getattr(args, "torrent_only", False):
        return "torrent"
    return getattr(args, "channel", "both")


def _format_doctor_text(payload: dict[str, Any]) -> str:
    lines = [
        "Quarry doctor",
        f"Python: {payload['python']}",
        f"stdout_encoding: {payload['stdout_encoding']}",
        f"cache_db: {payload['cache_db']}",
        f"storage_root: {payload['storage_root']}",
        f"yt-dlp: {payload['video']['binaries'].get('yt_dlp') or 'missing'}",
        f"ffmpeg: {payload['video']['binaries'].get('ffmpeg') or 'missing'}",
        f"download_dir: {payload['video']['download_dir']}",
        f"subtitle_dir: {payload['video']['subtitle_dir']}",
    ]
    lines.append("")
    lines.append(format_sources_text(payload["sources"]))

    # Zero-config coverage
    zc = payload.get("zero_config", {})
    if zc:
        lines.append("")
        lines.append("Zero-config coverage:")
        lines.append(f"  active:     {zc['active_count']}/{zc['total_count']} sources ({zc['coverage_pct']}%)")
        lines.append(f"  zero-conf:  {', '.join(zc['zero_config_sources'][:10])}")
        if zc.get("needs_token"):
            lines.append(f"  needs key:  {', '.join(zc['needs_token'])}")

    # Pan probe coverage
    pp = payload.get("pan_probe", {})
    if pp:
        lines.append("")
        lines.append("Pan link probe coverage:")
        lines.append(f"  supported:  {', '.join(pp['supported_providers'])}")
        lines.append(f"  count:      {pp['provider_count']} providers")

    if payload["video"].get("recent_manifests"):
        lines.append("")
        lines.append("Recent video manifests:")
        for item in payload["video"]["recent_manifests"]:
            lines.append(f"- {item.get('task_id', '-')}: {item.get('url')} [{item.get('preset') or item.get('lang') or '-'}]")

    # Mirror health
    mh = payload.get("mirror_health", {})
    if mh:
        lines.append("")
        lines.append("Mirror health:")
        for source, info in mh.items():
            healthy = info.get("healthy", 0)
            total = info.get("total", 0)
            lines.append(f"  {source}: {healthy}/{total} healthy")
            for m in info.get("mirrors", []):
                status = "✓" if m["ok"] else "✗"
                backoff = f" (backoff {m['backoff_remaining_s']}s)" if m.get("in_backoff") else ""
                lines.append(f"    {status} {m['mirror']} {m['latency_ms']}ms{backoff}")

    return "\n".join(lines)


def _search(engine: ResourceHunterEngine, args: argparse.Namespace) -> int:
    fast = getattr(args, "fast", False)
    intent = parse_intent(
        query=args.query,
        explicit_kind=_resolve_kind(args),
        channel=_resolve_channel(args),
        quick=args.quick or fast,
        wants_sub=args.sub,
        wants_4k=args.uhd,
    )
    if intent.is_video_url:
        video_manager = VideoManager(engine.cache)
        payload = video_manager.probe(intent.query)
        if args.json:
            print(dump_json(payload.to_dict()))
        else:
            print(format_video_text(payload, "probe"))
        return 0
    limit = min(args.limit, 5) if fast else args.limit
    probe = False if fast else (not args.no_probe)
    max_sources = 6 if fast else 0  # 0 = unlimited
    response = engine.search(intent, plan=build_plan(intent), page=args.page, limit=limit, use_cache=not args.no_cache, probe_links=probe, max_sources=max_sources)
    # Post-search filtering
    min_seeders = getattr(args, "min_seeders", 0)
    provider_filter = [p.strip().lower() for p in getattr(args, "provider", "").split(",") if p.strip()]
    if min_seeders or provider_filter:
        filtered = []
        for r in response.get("results", []):
            if min_seeders and r.get("channel") == "torrent" and (r.get("seeders") or 0) < min_seeders:
                continue
            if provider_filter and r.get("provider", "").lower() not in provider_filter:
                continue
            filtered.append(r)
        response["results"] = filtered
        response["meta"]["filtered"] = True
        response["meta"]["filter_min_seeders"] = min_seeders
        response["meta"]["filter_provider"] = provider_filter or None
    if args.json:
        if args.json_version == 2:
            print(dump_json(search_response_to_v2(response)))
        else:
            print(dump_json(response))
    else:
        print(format_search_text(response, max_results=min(limit, 4) if (args.quick or fast) else limit))
    return 0


def _sources(engine: ResourceHunterEngine, args: argparse.Namespace) -> int:
    payload = engine.source_catalog(probe=args.probe)
    if args.json:
        print(dump_json(payload))
    else:
        print(format_sources_text(payload))
    return 0


def _doctor(engine: ResourceHunterEngine, args: argparse.Namespace) -> int:
    from .pan_probe import _PROVIDER_PROBERS
    video_manager = VideoManager(engine.cache)

    # Zero-config coverage analysis
    _ZERO_CONFIG_SOURCES = {
        "upyunso", "panhunt", "nyaa", "dmhy", "bangumi_moe", "subsplease",
        "eztv", "torrentgalaxy", "bitsearch", "tpb", "yts", "1337x",
        "limetorrents", "torlock", "fitgirl", "torrentmac", "ext_to", "annas",
        "knaben", "btdig", "solidtorrents",
        "libgen", "torrentcsv", "glodls", "idope",
    }
    _TOKEN_SOURCES = {
        "ps.252035": "PANSOU_TOKEN",
        "pansou": "PANSOU_API_URL",
        "torznab": "TORZNAB_URL",
    }
    all_names = [a.name for a in engine.pan_sources + engine.torrent_sources]
    zero_conf_active = [n for n in all_names if n in _ZERO_CONFIG_SOURCES]
    needs_token: list[str] = []
    for src, var in _TOKEN_SOURCES.items():
        if src in all_names and not os.environ.get(var, "").strip():
            needs_token.append(f"{src} ({var})")

    payload = {
        "schema_version": "3",
        "python": sys.executable,
        "stdout_encoding": getattr(sys.stdout, "encoding", None),
        "cache_db": str(engine.cache.db_path),
        "storage_root": str(storage_root()),
        "sources": engine.source_catalog(probe=args.probe),
        "video": video_manager.doctor(),
        "zero_config": {
            "total_count": len(all_names),
            "active_count": len(zero_conf_active) + len([s for s, v in _TOKEN_SOURCES.items() if s in all_names and os.environ.get(v, "").strip()]),
            "coverage_pct": round(100 * (len(zero_conf_active) + len([s for s, v in _TOKEN_SOURCES.items() if s in all_names and os.environ.get(v, "").strip()])) / max(len(all_names), 1)),
            "zero_config_sources": zero_conf_active,
            "needs_token": needs_token,
        },
        "pan_probe": {
            "supported_providers": sorted(_PROVIDER_PROBERS.keys()),
            "provider_count": len(_PROVIDER_PROBERS),
        },
    }
    # Add mirror health if any mirrors have been tracked
    try:
        from .mirror_health import get_mirror_tracker
        mh_summary = get_mirror_tracker().summary()
        if mh_summary:
            payload["mirror_health"] = mh_summary
    except Exception:
        pass
    if args.json:
        print(dump_json(payload))
    else:
        print(_format_doctor_text(payload))
    return 0


def _video(engine: ResourceHunterEngine, args: argparse.Namespace) -> int:
    video_manager = VideoManager(engine.cache)
    if args.video_cmd == "info":
        payload = video_manager.info(args.url)
    elif args.video_cmd == "probe":
        payload = video_manager.probe(args.url)
    elif args.video_cmd == "download":
        payload = video_manager.download(args.url, preset=args.format, output_dir=args.dir)
    elif args.video_cmd == "subtitle":
        payload = video_manager.subtitle(args.url, lang=args.lang)
    else:
        raise RuntimeError(f"unsupported video command: {args.video_cmd}")
    if getattr(args, "json", False):
        print(dump_json(payload.to_dict()))
    else:
        print(format_video_text(payload, args.video_cmd))
    return 0


def _benchmark(engine: ResourceHunterEngine, args: argparse.Namespace) -> int:
    payload = engine.run_benchmark()
    if args.json:
        print(dump_json(payload))
    else:
        print(format_benchmark_text(payload))
    return 0 if payload.get("pass") else 1


def _cache(engine: ResourceHunterEngine, args: argparse.Namespace) -> int:
    if args.cache_cmd == "cleanup":
        deleted = engine.cache.cleanup(max_age_seconds=args.max_age)
        payload = {"action": "cleanup", "max_age_seconds": args.max_age, "deleted": deleted}
        if args.json:
            print(dump_json(payload))
        else:
            print("Cache cleanup completed:")
            for table, count in deleted.items():
                print(f"  {table}: {count} rows removed")
    else:
        payload = {
            "action": "stats",
            "db_path": str(engine.cache.db_path),
            "db_size_mb": round(engine.cache.db_path.stat().st_size / 1024 / 1024, 2) if engine.cache.db_path.exists() else 0,
        }
        if args.json:
            print(dump_json(payload))
        else:
            print(f"Cache database: {payload['db_path']}")
            print(f"Size: {payload['db_size_mb']} MB")
    return 0


def _subtitle(args: argparse.Namespace) -> int:
    source = getattr(args, "source", "all")
    kind = ""
    if getattr(args, "movie", False):
        kind = "movie"
    elif getattr(args, "tv", False):
        kind = "tv"
    season = getattr(args, "season", None)
    episode = getattr(args, "episode", None)

    all_subtitles: list[dict] = []
    data: dict[str, Any] = {"status": True, "results": [], "subtitles": []}

    
    if source in ("all", "subdl"):
        subdl = SubDLClient()
        subdl_data = subdl.search(
            args.query, kind=kind, season=season, episode=episode,
            languages=args.lang, limit=args.limit,
        )
        if source == "subdl":
            data = subdl_data
        else:
            data["results"] = subdl_data.get("results", [])
            for s in subdl_data.get("subtitles", []):
                s.setdefault("source", "subdl")
                all_subtitles.append(s)

    
    if source in ("all", "subhd"):
        subhd = SubHDClient()
        subhd_data = subhd.search(
            args.query, season=season, episode=episode, limit=args.limit,
        )
        if source == "subhd":
            data = subhd_data
        else:
            for s in subhd_data.get("subtitles", []):
                all_subtitles.append(s)

    
    if source in ("all", "jimaku"):
        jimaku = JimakuClient()
        jimaku_data = jimaku.search(
            args.query, episode=episode, limit=args.limit,
        )
        if source == "jimaku":
            data = jimaku_data
        else:
            for s in jimaku_data.get("subtitles", []):
                all_subtitles.append(s)

    # Merge results for 'all' mode
    if source == "all":
        data["subtitles"] = all_subtitles[:args.limit]

    # Auto-download best match
    artifacts: list[dict] = []
    if args.download and data.get("subtitles"):
        best = data["subtitles"][0]
        dl_url = best.get("download_url", "")
        if dl_url:
            try:
                src = best.get("source", "subdl")
                if src == "jimaku":
                    artifacts = JimakuClient().download(dl_url, output_dir=getattr(args, "dir", None))
                else:
                    artifacts = SubDLClient().download(dl_url, output_dir=getattr(args, "dir", None))
            except RuntimeError as exc:
                if not args.json:
                    print(f"Download failed: {exc}")

    if args.json:
        from .common import dump_json
        payload = {
            "schema_version": "3",
            "query": args.query,
            "lang": args.lang,
            "source": source,
            **data,
        }
        if artifacts:
            payload["downloaded"] = artifacts
        print(dump_json(payload))
    else:
        print(format_subtitle_results(data, artifacts))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Quarry")
    sub = parser.add_subparsers(dest="command")

    p_search = sub.add_parser("search", help="Search public pan/torrent resources")
    p_search.add_argument("query", help="keyword or public video url")
    p_search.add_argument("--kind", choices=["movie", "tv", "anime", "music", "software", "book", "general"])
    p_search.add_argument("--channel", choices=["both", "pan", "torrent"], default="both")
    p_search.add_argument("--movie", action="store_true")
    p_search.add_argument("--tv", action="store_true")
    p_search.add_argument("--anime", action="store_true")
    p_search.add_argument("--music", action="store_true")
    p_search.add_argument("--software", action="store_true")
    p_search.add_argument("--book", action="store_true")
    p_search.add_argument("--general", action="store_true")
    p_search.add_argument("--pan-only", action="store_true")
    p_search.add_argument("--torrent-only", action="store_true")
    p_search.add_argument("--page", type=int, default=1)
    p_search.add_argument("--limit", type=int, default=8)
    p_search.add_argument("--quick", action="store_true")
    p_search.add_argument("--sub", action="store_true")
    p_search.add_argument("--4k", action="store_true", dest="uhd")
    p_search.add_argument("--json", action="store_true")
    p_search.add_argument("--json-version", choices=[2, 3], type=int, default=3)
    p_search.add_argument("--no-cache", action="store_true")
    p_search.add_argument("--no-probe", action="store_true", help="Skip pan link viability probing")
    p_search.add_argument("--fast", action="store_true", help="Fast mode: top 6 sources, no probe, limit 5")
    p_search.add_argument("--min-seeders", type=int, default=0, help="Minimum seeders for torrent results")
    p_search.add_argument("--provider", type=str, default="", help="Comma-separated provider filter (e.g. aliyun,quark)")

    p_sources = sub.add_parser("sources", help="Show configured resource sources")
    p_sources.add_argument("--probe", action="store_true")
    p_sources.add_argument("--json", action="store_true")

    p_doctor = sub.add_parser("doctor", help="Check dependencies and cached health")
    p_doctor.add_argument("--probe", action="store_true")
    p_doctor.add_argument("--json", action="store_true")

    p_benchmark = sub.add_parser("benchmark", help="Run the offline benchmark suite")
    p_benchmark.add_argument("--json", action="store_true")

    p_video = sub.add_parser("video", help="Video workflow powered by yt-dlp")
    video_sub = p_video.add_subparsers(dest="video_cmd", required=True)

    p_info = video_sub.add_parser("info", help="Fetch video metadata")
    p_info.add_argument("url")
    p_info.add_argument("--json", action="store_true")

    p_probe = video_sub.add_parser("probe", help="Probe a video url without download")
    p_probe.add_argument("url")
    p_probe.add_argument("--json", action="store_true")

    p_download = video_sub.add_parser("download", help="Download a public video")
    p_download.add_argument("url")
    p_download.add_argument("format", nargs="?", default="best")
    p_download.add_argument("--dir")
    p_download.add_argument("--json", action="store_true")

    p_subtitle = video_sub.add_parser("subtitle", help="Extract subtitles")
    p_subtitle.add_argument("url")
    p_subtitle.add_argument("--lang", default="zh-Hans,zh,en")
    p_subtitle.add_argument("--json", action="store_true")

    p_sub = sub.add_parser("subtitle", help="Search and download subtitles (SubDL + SubHD + Jimaku)")
    p_sub.add_argument("query", help="Movie or TV show title")
    p_sub.add_argument("--lang", default="zh,en", help="Comma-separated language codes")
    p_sub.add_argument("--season", type=int, default=None)
    p_sub.add_argument("--episode", type=int, default=None)
    p_sub.add_argument("--movie", action="store_true")
    p_sub.add_argument("--tv", action="store_true")
    p_sub.add_argument("--source", choices=["all", "subdl", "subhd", "jimaku"], default="all",
                       help="Subtitle source (default: all)")
    p_sub.add_argument("--limit", type=int, default=10)
    p_sub.add_argument("--download", action="store_true", help="Auto-download best match")
    p_sub.add_argument("--dir", default=None, help="Output directory for downloads")
    p_sub.add_argument("--json", action="store_true")

    p_cache = sub.add_parser("cache", help="Cache management")
    p_cache.add_argument("cache_cmd", choices=["cleanup", "stats"], nargs="?", default="stats")
    p_cache.add_argument("--max-age", type=int, default=86400, help="Max age in seconds for cleanup")
    p_cache.add_argument("--json", action="store_true")

    p_history = sub.add_parser("history", help="View search history")
    p_history.add_argument("--limit", type=int, default=20, help="Number of entries to show")
    p_history.add_argument("--json", action="store_true")
    p_history.add_argument("--export", choices=["csv", "markdown"], default=None, help="Export format")
    return parser


def _history(engine: ResourceHunterEngine, args: argparse.Namespace) -> int:
    history = engine.cache.list_history(limit=args.limit)
    if args.json:
        print(dump_json({"schema_version": "3", "history": history}))
        return 0
    if args.export == "csv":
        import csv
        import io
        output = io.StringIO()
        if history:
            writer = csv.DictWriter(output, fieldnames=list(history[0].keys()))
            writer.writeheader()
            writer.writerows(history)
        print(output.getvalue())
        return 0
    if args.export == "markdown":
        if not history:
            print("No search history.")
            return 0
        print("| Query | Kind | Channel | Results | Top Source | Time |")
        print("|:------|:-----|:--------|--------:|:-----------|:-----|")
        for h in history:
            print(f"| {h['query']} | {h['kind']} | {h['channel']} | {h['result_count']} | {h['top_source']} | {h['searched_at'][:19]} |")
        return 0
    # Default text format
    if not history:
        print("No search history.")
        return 0
    print(f"Recent searches ({len(history)} entries):")
    print()
    for h in history:
        ts = h['searched_at'][:19].replace('T', ' ')
        results = h['result_count']
        print(f"  [{ts}] \"{h['query']}\" → {results} results ({h['kind']}/{h['channel']}) top={h['top_source'] or '-'}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ensure_utf8_stdio()
    argv = list(argv if argv is not None else sys.argv[1:])
    if argv and argv[0] not in {"search", "sources", "doctor", "video", "benchmark", "cache", "subtitle", "history"}:
        argv = ["search"] + argv

    parser = build_parser()
    args = parser.parse_args(argv)
    cache = ResourceCache()
    engine = ResourceHunterEngine(cache=cache)

    try:
        if args.command == "search":
            return _search(engine, args)
        if args.command == "sources":
            return _sources(engine, args)
        if args.command == "doctor":
            return _doctor(engine, args)
        if args.command == "video":
            return _video(engine, args)
        if args.command == "benchmark":
            return _benchmark(engine, args)
        if args.command == "subtitle":
            return _subtitle(args)
        if args.command == "cache":
            return _cache(engine, args)
        if args.command == "history":
            return _history(engine, args)
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    parser.print_help()
    return 0

