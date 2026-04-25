# Resource Hunter Sources

## Pan sources

- `upyunso`
  - Channel: `pan`
  - Priority: `1`
  - Role: primary pan aggregator (UP云搜, high volume, free)
  - Auth: none (uses AES-encrypted API, requires `pycryptodome`)
  - Supports: 夸克/阿里/百度/迅雷/UC/蓝奏/天翼
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
- `hunhepan`
  - Channel: `pan`
  - Priority: `3`
  - Role: fallback pan source
  - Auth: `HUNHEPAN_TOKEN` (required)
  - Note: default_degraded due to historical instability

## Torrent sources
- `torznab`
  - Channel: `torrent`
  - Priority: `1`
  - Role: Universal Meta-Indexer (Jackett / Prowlarr)
  - Auth: `TORZNAB_URL` and `TORZNAB_APIKEY`
  - API: Standard Torznab XML feed
  - Best for: Unlocking 500+ trackers (like 1337x, RARBG clones, TorrentGalaxy, Rutracker) and bypassing Cloudflare natively.
- `bitsearch`
  - Channel: `torrent`
  - Priority: `2`
  - Role: High-speed, high-availability native magnet indexer (formerly SolidTorrents)
  - API: HTML scraper
- `nyaa`
  - Channel: `torrent`
  - Priority: `1`
  - Best for anime
  - API: RSS/XML feed
- `eztv`
  - Channel: `torrent`
  - Priority: `1`
  - Best for TV episodes
  - API: JSON REST
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
- `annas`
  - Channel: `torrent`
  - Priority: `2`
  - Best for: Books and ebooks (PDF, EPUB, MOBI)
  - API: HTML scraper on Anna's Archive search
  - Note: Protected by DDoS-Guard; benefits from `curl_cffi` for TLS impersonation

## Default routing matrix

- Movie: `upyunso -> ps.252035 -> panhunt -> hunhepan`, then `yts -> tpb -> 1337x -> limetorrents`
- TV: `eztv -> tpb -> 1337x -> limetorrents`, then pan sources
- Anime: `nyaa -> tpb -> 1337x -> limetorrents`, then pan sources
- Book: `annas -> torznab -> bitsearch -> 1337x`, then pan sources
- Music/software/general: pan sources first, torrent sources second
- Public video URL: no pan/torrent search; route directly to video workflow

## Capability profile notes

- Every source has a fixed timeout, retry count, query budget, and cooldown threshold
- Default-degraded sources require repeated success evidence before they are treated as healthy again

## Health and circuit breaking

- Every active search stores a `source_status` record with `degraded_reason`, `recovery_state`, and `last_success_epoch`
- Repeated recent failures can temporarily open a circuit for that source
- `sources --probe` actively tests current reachability
- `doctor` reports cached recent source state

## Environment variables

| Variable | Used by | Required |
|----------|---------|----------|
| `PANSOU_TOKEN` | ps.252035, panhunt | Yes (ps.252035), Optional (panhunt) |
| `HUNHEPAN_TOKEN` | hunhepan | Yes |
| `TORZNAB_URL` | torznab | Yes (when using torznab) |
| `TORZNAB_APIKEY` | torznab | Yes (when using torznab) |
| `HTTP_PROXY` / `HTTPS_PROXY` | HTTPClient | Optional (needed for CN servers) |

## Pan link viability probing

- Pan search results (`top` / `related` tier) are automatically probed for link viability
- Supported providers: Aliyun (阿里云盘), Quark (夸克网盘), Baidu (百度网盘)
- Dead links are demoted to `risky` tier with penalty `dead link detected`
- Probe results are available in `source_health.link_alive` (true/false/null)
- Use `--no-probe` to skip probing for faster results

## Anti-bot: curl_cffi

- Optional: `pip install curl-cffi` enables TLS fingerprint impersonation (JA3/JA4)
- HTTPClient priority chain: `httpx → curl_cffi → urllib`
- Helps bypass DDoS-Guard (Anna's Archive) and similar network-level bot detection
- No configuration needed — auto-detected at import time

## Caveats

- External public sources may throttle, change formats, or break without notice
- Coverage quality varies by query and source index freshness
- hunhepan is default_degraded and requires token; without it, effectively only 2 pan sources are active
