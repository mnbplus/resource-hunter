# Quarry Sources

## Pan sources

- `upyunso`
  - Channel: `pan`
  - Priority: `1`
  - Role: primary pan aggregator (UP云搜, high volume, free)
  - Auth: none (uses AES-encrypted API, requires `pycryptodome`)
  - Supports: 夸克/阿里/百度/迅雷/UC/蓝奏/天翼
- `pansou`
  - Channel: `pan`
  - Priority: `1`
  - Role: self-hosted pan aggregation API (13+ cloud providers)
  - Auth: `PANSOU_API_URL` (required), `PANSOU_API_TOKEN` (optional)
  - Supports: 夸克/阿里/百度/115/PikPak/天翼/UC/迅雷/123 + magnet/ed2k
- `ps.252035`
  - Channel: `pan`
  - Priority: `1`
  - Role: secondary pan aggregator
  - Auth: `PANSOU_TOKEN` (required)
- `panhunt`
  - Channel: `pan`
  - Priority: `2`
  - Role: secondary pan aggregator
  - Auth: `PANSOU_TOKEN` (optional, improves results)

## Torrent sources

- `torznab`
  - Channel: `torrent`
  - Priority: `1`
  - Role: Universal Meta-Indexer (Jackett / Prowlarr)
  - Auth: `TORZNAB_URL` and `TORZNAB_APIKEY`
  - API: Standard Torznab XML feed
  - Best for: Unlocking 500+ trackers (like 1337x, RARBG clones, TorrentGalaxy, Rutracker) and bypassing Cloudflare natively.
- `nyaa`
  - Channel: `torrent`
  - Priority: `1`
  - Best for anime
  - API: RSS/XML feed
- `dmhy`
  - Channel: `torrent`
  - Priority: `1`
  - Best for: Chinese anime community (動漫花園)
  - API: RSS/XML feed
  - Supported kinds: anime, music, general
- `bangumi_moe`
  - Channel: `torrent`
  - Priority: `1`
  - Best for: Anime torrents (Bangumi Moe)
  - API: JSON REST
  - Supported kinds: anime, general
- `subsplease`
  - Channel: `torrent`
  - Priority: `1`
  - Best for: Anime fansub releases (SubsPlease)
  - API: JSON REST
  - Supported kinds: anime
- `eztv`
  - Channel: `torrent`
  - Priority: `1`
  - Best for TV episodes
  - API: JSON REST
- `torrentgalaxy`
  - Channel: `torrent`
  - Priority: `2`
  - Best for: General-purpose tracker (RARBG replacement), movies, TV, games, software, music
  - API: HTML scraper with mirror failover
  - Note: Category-aware search (maps intent kind to TorrentGalaxy categories)
- `bitsearch`
  - Channel: `torrent`
  - Priority: `2`
  - Role: High-speed, high-availability native magnet indexer (formerly SolidTorrents)
  - API: HTML scraper
- `tpb`
  - Channel: `torrent`
  - Priority: `2`
  - General fallback
  - API: JSON REST (apibay.org)
- `yts`
  - Channel: `torrent`
  - Priority: `2`
  - Best for movies
  - API: JSON REST
- `1337x`
  - Channel: `torrent`
  - Priority: `3`
  - General supplementary source
  - API: HTML scraper with mirror failover
- `limetorrents`
  - Channel: `torrent`
  - Priority: `3`
  - General supplementary source
  - API: RSS/XML feed with mirror failover
  - Note: default_degraded
- `torlock`
  - Channel: `torrent`
  - Priority: `3`
  - Best for: Verified torrent index with seeders/leechers
  - API: HTML scraper
- `fitgirl`
  - Channel: `torrent`
  - Priority: `3`
  - Best for: Repacked PC Games and Software
  - API: RSS/XML feed
- `torrentmac`
  - Channel: `torrent`
  - Priority: `3`
  - Best for: Mac software and games
  - API: HTML scraper with concurrent detail-page extraction
- `ext_to`
  - Channel: `torrent`
  - Priority: `3`
  - Best for: Modern magnet search engine
  - API: HTML scraper
  - Note: default_degraded

## Book sources

- `annas`
  - Channel: `torrent`
  - Priority: `2`
  - Best for: Books and ebooks (PDF, EPUB, MOBI)
  - API: HTML scraper on Anna's Archive search
  - Note: Protected by DDoS-Guard; benefits from `curl_cffi` for TLS impersonation; default_degraded
- `libgen`
  - Channel: `torrent`
  - Priority: `2`
  - Best for: Books, ebooks, academic papers (PDF, EPUB, MOBI, DJVU)
  - API: HTML scraper on Library Genesis with multi-mirror fallback (libgen.rs/is/st)
  - Note: default_degraded; direct download links via MD5 hash

## DHT / backup sources

- `knaben`
  - Channel: `torrent`
  - Priority: `3`
  - Best for: General-purpose torrent meta-search (combines multiple public indexers)
  - API: JSON POST
- `btdig`
  - Channel: `torrent`
  - Priority: `4`
  - Best for: DHT network crawler (finds rare torrents)
  - API: HTML scraper
  - Note: default_degraded
- `solidtorrents`
  - Channel: `torrent`
  - Priority: `4`
  - Best for: General-purpose torrent search
  - API: HTML scraper
  - Note: default_degraded
- `torrentcsv`
  - Channel: `torrent`
  - Priority: `3`
  - Best for: Open-source DHT torrent search
  - API: JSON REST (clean, well-structured)
- `glodls`
  - Channel: `torrent`
  - Priority: `4`
  - Best for: General downloads (movies, TV, software, music)
  - API: HTML scraper
  - Note: default_degraded
- `idope`
  - Channel: `torrent`
  - Priority: `4`
  - Best for: DHT network torrent search (broad coverage)
  - API: HTML scraper with dual-strategy parsing
  - Note: default_degraded

## Default routing matrix

- Movie: pan sources, then `yts → torrentgalaxy → bitsearch → tpb → 1337x → limetorrents → torlock → ext_to → knaben → solidtorrents → btdig → torrentcsv → glodls → idope`
- TV: `eztv → torrentgalaxy → bitsearch → tpb → 1337x → limetorrents → torlock → ext_to → knaben → solidtorrents → btdig → torrentcsv → glodls → idope`, then pan sources
- Anime: `nyaa → dmhy → bangumi_moe → bitsearch → torrentgalaxy → tpb → 1337x → limetorrents → torlock → ext_to → knaben → solidtorrents → btdig → torrentcsv → idope`, then pan sources
- Book: `annas → libgen → torznab → bitsearch → 1337x → limetorrents → torrentgalaxy → torlock → ext_to → tpb → knaben → solidtorrents → btdig → torrentcsv → idope`, then pan sources
- Music: pan sources first, then `nyaa → dmhy → bitsearch → 1337x → torrentgalaxy → ext_to → knaben → solidtorrents → btdig → torrentcsv → idope` (noise-filtered, no tpb)
- Software: pan sources first, then `torrentmac → fitgirl → torrentgalaxy → bitsearch → tpb → 1337x → limetorrents → torlock → ext_to → knaben → solidtorrents → btdig → torrentcsv → glodls → idope`
- General: pan sources first, all torrent sources

## Capability profile notes

- Every source has a fixed timeout, retry count, query budget, and cooldown threshold
- Default-degraded sources (limetorrents, annas, ext_to) require repeated success evidence before they are treated as healthy again

## Health and circuit breaking

- Every active search stores a `source_status` record with `degraded_reason`, `recovery_state`, and `last_success_epoch`
- Repeated recent failures can temporarily open a circuit for that source
- `sources --probe` actively tests current reachability
- `doctor` reports cached recent source state

## Environment variables

| Variable | Used by | Required |
|----------|---------|----------|
| `PANSOU_TOKEN` | ps.252035, panhunt | Yes (ps.252035), Optional (panhunt) |
| `PANSOU_API_URL` | pansou | Yes (when using pansou) |
| `PANSOU_API_TOKEN` | pansou | Optional (only if AUTH_ENABLED=true) |
| `TORZNAB_URL` | torznab | Yes (when using torznab) |
| `TORZNAB_APIKEY` | torznab | Yes (when using torznab) |
| `HTTP_PROXY` / `HTTPS_PROXY` | HTTPClient | Optional (needed for CN servers) |

## Pan link viability probing

- Pan search results (`top` / `related` tier) are automatically probed for link viability
- Supported providers (7):
  - **Aliyun (阿里云盘)** — anonymous share API
  - **Quark (夸克网盘)** — share token API
  - **Baidu (百度网盘)** — share page dead-signal detection
  - **Lanzou (蓝奏云)** — share page dead-signal detection (all domain variants)
  - **Tianyi (天翼云盘)** — share info API
  - **115 (115网盘)** — page-level status detection (conservative due to anti-bot)
  - **PikPak** — share info API
- Dead links are demoted to `risky` tier with penalty `dead link detected`
- Probe results are available in `source_health.link_alive` (true/false/null)
- Use `--no-probe` to skip probing for faster results
- All probes are zero-login: no cookies, tokens, or accounts required

## Anti-bot: curl_cffi

- Optional: `pip install curl-cffi` enables TLS fingerprint impersonation (JA3/JA4)
- HTTPClient priority chain: `httpx → curl_cffi → urllib`
- Helps bypass DDoS-Guard (Anna's Archive) and similar network-level bot detection
- No configuration needed — auto-detected at import time

## Caveats

- External public sources may throttle, change formats, or break without notice
- Coverage quality varies by query and source index freshness
- pansou requires `PANSOU_API_URL` to be set; without it, the source silently returns empty results
