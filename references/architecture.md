# Quarry Architecture

## Layout

- `scripts/hunt.py`: primary CLI entrypoint
- `scripts/quarry/models.py`: public data models
- `scripts/quarry/common.py`: parsing, normalization, release tags, and filesystem helpers
- `scripts/quarry/parsers.py`: release tag / quality parsing
- `scripts/quarry/url_utils.py`: URL, provider, platform detection
- `scripts/quarry/text_utils.py`: title normalization, tokenization, language detection
- `scripts/quarry/config.py`: configurable ranking weights and scoring parameters
- `scripts/quarry/cache.py`: SQLite-backed cache and manifest storage
- `scripts/quarry/intent.py`: query parsing, alias resolution, and query-family generation
- `scripts/quarry/sources/`: plugin-based source adapters (see below)
- `scripts/quarry/ranking.py`: identity, scoring, tiers, dedupe, diversity reorder
- `scripts/quarry/rendering.py`: text renderers and compatibility transforms
- `scripts/quarry/engine.py`: orchestration, cache integration, source health, benchmark entrypoint
- `scripts/quarry/benchmark.py`: offline deterministic benchmark suite
- `scripts/quarry/video_core.py`: yt-dlp workflow and manifest handling
- `scripts/quarry/cli.py`: unified CLI surface
- `scripts/quarry/exceptions.py`: custom exception hierarchy
- `scripts/quarry/_cleanup.py`: deprecated file auto-removal on startup

## Source adapters (plugin architecture)

```text
sources/
  __init__.py         # SourceRegistry, default_adapters()
  base.py             # SourceAdapter, HTTPClient, BrowserClient, SourceRuntimeProfile, helpers

  # Pan sources (cloud drive aggregators)
  upyunso.py          # UP云搜 pan aggregator (requires pycryptodome)
  pansou.py           # PanSou self-hosted pan aggregation API (PANSOU_API_URL + PANSOU_API_TOKEN)
  ps252035.py         # ps.252035.xyz pan search (requires PANSOU_TOKEN)
  panhunt.py          # s.panhunt.com pan search (optional PANSOU_TOKEN)

  # Torrent sources
  torznab.py          # Torznab meta-indexer (Jackett / Prowlarr)
  nyaa.py             # Nyaa.si anime/general torrent (RSS)
  dmhy.py             # 動漫花園 Chinese anime community tracker (RSS)
  bangumi_moe.py      # Bangumi Moe anime torrent (JSON API)
  eztv.py             # EZTV TV torrent (JSON API)
  torrentgalaxy.py    # TorrentGalaxy general tracker, RARBG replacement (HTML scraper with mirror failover)
  bitsearch.py        # Bitsearch magnet indexer (HTML scraper)
  tpb.py              # ThePirateBay (apibay) torrent (JSON API)
  yts.py              # YTS movie torrent (JSON API)
  x1337.py            # 1337x torrent (HTML scraper with mirror failover)
  limetorrents.py     # LimeTorrents (RSS/XML feed with mirror failover)
  torlock.py          # TorLock verified torrent index (HTML scraper)
  fitgirl.py          # FitGirl Repacks (RSS/XML feed)
  torrentmac.py       # TorrentMac (HTML scraper with concurrent detail-page extraction)
  ext_to.py           # EXT.to modern magnet search engine (HTML scraper)
  subsplease.py       # SubsPlease anime fansub group tracker (JSON API)

  # Book sources
  annas.py            # Anna's Archive ebook search (HTML scraper, DDoS-Guard protected)
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
