# Resource Hunter Architecture

## Layout

- `scripts/hunt.py`: primary CLI entrypoint
- `scripts/resource_hunter/models.py`: public data models
- `scripts/resource_hunter/common.py`: parsing, normalization, release tags, and filesystem helpers
- `scripts/resource_hunter/parsers.py`: release tag / quality parsing
- `scripts/resource_hunter/url_utils.py`: URL, provider, platform detection
- `scripts/resource_hunter/text_utils.py`: title normalization, tokenization, language detection
- `scripts/resource_hunter/config.py`: configurable ranking weights and scoring parameters
- `scripts/resource_hunter/cache.py`: SQLite-backed cache and manifest storage
- `scripts/resource_hunter/intent.py`: query parsing, alias resolution, and query-family generation
- `scripts/resource_hunter/sources/`: plugin-based source adapters (see below)
- `scripts/resource_hunter/ranking.py`: identity, scoring, tiers, dedupe, diversity reorder
- `scripts/resource_hunter/rendering.py`: text renderers and compatibility transforms
- `scripts/resource_hunter/engine.py`: orchestration, cache integration, source health, benchmark entrypoint
- `scripts/resource_hunter/benchmark.py`: offline deterministic benchmark suite
- `scripts/resource_hunter/video_core.py`: yt-dlp workflow and manifest handling
- `scripts/resource_hunter/cli.py`: unified CLI surface
- `scripts/resource_hunter/exceptions.py`: custom exception hierarchy

## Source adapters (plugin architecture)

```text
sources/
  __init__.py       # SourceRegistry, default_adapters()
  base.py           # SourceAdapter, HTTPClient, BrowserClient, SourceRuntimeProfile, helpers
  upyunso.py        # UP云搜 pan aggregator (requires pycryptodome)
  ps252035.py       # ps.252035.xyz pan search (requires PANSOU_TOKEN)
  panhunt.py        # s.panhunt.com pan search (optional PANSOU_TOKEN)
  hunhepan.py       # Hunhepan pan aggregator (requires HUNHEPAN_TOKEN)
  torznab.py        # Torznab meta-indexer (Jackett / Prowlarr)
  nyaa.py           # Nyaa.si anime/general torrent (RSS)
  eztv.py           # EZTV TV torrent (JSON API)
  bitsearch.py      # Bitsearch magnet indexer (HTML scraper)
  tpb.py            # ThePirateBay (apibay) torrent (JSON API)
  yts.py            # YTS movie torrent (JSON API)
  x1337.py          # 1337x torrent (HTML scraper with mirror failover)
  limetorrents.py   # LimeTorrents (RSS/XML feed with mirror failover)
  fitgirl.py        # FitGirl Repacks (RSS/XML feed)
  torrentmac.py     # TorrentMac (HTML scraper with concurrent detail-page extraction)
```

## Search flow

1. Parse query into `SearchIntent`
2. Resolve public alias metadata when eligible
3. Build a category-specific `SearchPlan`
4. Route to pan/torrent/video pipeline
5. Query source adapters with fixed query families and per-source query budgets
6. Normalize all results into `SearchResult`
7. Score, tier, deduplicate, and diversify
8. Render text output or JSON
9. Cache response, source health, and video manifests

## Source adapter contract

Each adapter implements:

- `search(query, intent, limit, page, http_client) -> list[SearchResult]`
- `healthcheck(http_client) -> (ok, error)`

All adapter outputs must already be normalized into the shared `SearchResult` structure.

## Cache

SQLite database stores:

- `search_cache`: short-TTL normalized responses
- `source_status`: rolling source health results, degraded reasons, and recovery state
- `video_manifest`: download/subtitle artifacts keyed by task id
- `alias_resolution`: cached public alias expansion for multilingual movie queries

The circuit breaker skips sources that have failed repeatedly in the recent cooldown window.

## JSON schema

Top level search payload:

```json
{
  "schema_version": "3",
  "query": "...",
  "intent": {},
  "plan": {},
  "results": [],
  "suppressed": [],
  "warnings": [],
  "source_status": [],
  "meta": {}
}
```

Top level video payload:

```json
{
  "url": "...",
  "platform": "...",
  "title": "...",
  "duration": 0,
  "formats": [],
  "recommended": [],
  "artifacts": [],
  "meta": {}
}
```

## Benchmark

- `benchmark` runs 180 offline search fixtures and 20 video fixtures
- gates: overall Top1/Top3, per-kind Top1/Top3, high-confidence error rate, adversarial top-failure rate, video URL classification
