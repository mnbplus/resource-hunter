# Changelog

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
