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

### Removed Sources

- **HunHePan** — Deprecated in favor of PanSou and other pan aggregators (auto-cleaned on startup)

### Improvements

- **HTTPClient**: Enhanced with improved error handling and retry logic
- **Source Registry**: Expanded routing matrix for anime (Nyaa/DMHY/Bangumi Moe first) and software (TorrentGalaxy/FitGirl/TorrentMac)
- **Ranking**: Refined scoring with adult content filtering and better music/book category handling
- **Engine**: Optimized concurrent source fan-out with dynamic thread pool sizing
- **Intent**: Updated preferred source lists for all categories to include new adapters
- **Pan Probe**: Minor reliability improvements
- **Benchmark**: Updated fixture suite for expanded source coverage
- **Tests**: Updated precision and intent tests for new source routing

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
