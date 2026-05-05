# Changelog

## 1.1.0

### New Sources

- **PanSou** — Self-hosted pan aggregation API with JWT authentication
- **DMHY (動漫花園)** — Chinese anime community tracker via RSS
- **Bangumi Moe** — Anime torrent tracker with JSON API
- **TorrentGalaxy** — General tracker (RARBG alternative) with rich metadata
- **TorLock** — Verified torrent index with seeders/leechers
- **EXT.to** — Modern magnet search engine
- **SubsPlease** — Anime fansub group tracker
- **Knaben** — Torrent meta-search engine aggregating 30+ trackers via JSON API
- **BTDigg** — DHT network indexer for long-tail/niche content via HTML scraping
- **SolidTorrents** — Public torrent search with JSON API + HTML scraper fallback
- **Libgen** — Library Genesis book/ebook/paper search via HTML scraping with multi-mirror fallback
- **TorrentCSV** — Open-source DHT torrent search with clean JSON API
- **GLODLS** — Global Downloads public torrent index via HTML scraping
- **iDope** — DHT network torrent search engine with dual-strategy HTML parsing

### Removed Sources

- **HunHePan** — Deprecated in favor of PanSou and other pan aggregators (auto-cleaned on startup)

### New Features

- **`--fast` mode**: Queries only top 6 sources, skips probe, limits to 5 results. Target latency <3s for Agent conversations
- **`summary` field**: JSON output now includes a natural-language result summary for AI Agents
- **`--min-seeders N`**: Post-search filter for minimum seeder count on torrent results
- **`--provider`**: Post-search filter by cloud drive provider name (e.g. `--provider aliyun,quark`)
- **Structured exceptions**: `SourceNetworkError`, `SourceParseError`, `SourceRateLimitError` now recognized by failure classifier

### Bug Fixes

- **source_priority()**: Added all new sources to priority dictionary — previously silently getting lowest score
- **Cache key**: Now includes `probe_links` parameter; `--no-probe` results no longer contaminate normal cache
- **Future handling**: `engine.py` now wraps `future.result()` in try/except for graceful degradation
- **Agent contract sync**: SKILL.md, hermes.yaml, openclaw.yaml fully synced with source routing and Libgen guidance

### Improvements

- **Source Count**: 15 → **28** total sources (25 zero-config, 3 need API keys/tokens)
- **Zero-Config Coverage**: 89% of sources work without any configuration
- **Book Routing**: Anna's Archive + Libgen as dual primary sources for ebook discovery
- **DHT Fallback Layer**: TorrentCSV, GLODLS, iDope, BTDigg, SolidTorrents as broad backup
- **Anime Routing**: Nyaa/DMHY/Bangumi Moe/SubsPlease as primary sources
- **Ranking**: Refined scoring with adult content filtering, music/book category handling, diversity pass
- **Engine**: Optimized concurrent source fan-out with dynamic thread pool sizing and `max_sources` cap
- **`search_response_to_v2()`**: Marked as deprecated, will be removed in a future version
- **Documentation**: Updated README, README.zh-CN, references/sources.md, CHANGELOG with full 28-source coverage
- **Tests**: 29 automated tests covering routing, ranking, precision, zero-config coverage, and CLI

## 1.0.0

Initial release.

### Features

- Multi-source search across 15 adapters (4 pan aggregators, 10 torrent indexers, 1 book source)
- Quality-aware ranking with title-family matching, season/episode parsing, and release tag scoring
- Pan link viability probing (Aliyun, Quark, Baidu) with automatic dead-link demotion
- Anti-bot defense chain: httpx → curl_cffi → urllib (auto-fallback)
- Anna's Archive integration for ebook/book discovery
- Public video pipeline via yt-dlp (probe, download, subtitle extraction)
- Subtitle search via SubDL, SubHD, and Jimaku adapters
- JSON v3 output contract with confidence scoring, match buckets, and source health
- Local customization safe zone (`local/`) for user sources, config overrides, and env vars
- Auto-cleanup of deprecated files on startup (safe ZIP updates)
- SQLite cache with TTL, source health tracking, and circuit breaking
- 22 automated tests including offline benchmark suite (180 search + 20 video fixtures)
- OpenClaw/Hermes skill integration via SKILL.md with full frontmatter metadata
