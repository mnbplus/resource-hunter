---
name: resource-hunter
version: 1.0.0
description: >-
  Use when finding public download routes for movies, TV, anime, music,
  software, or books. Searches pan/torrent/book sources with quality-aware
  ranking, link viability probing, and structured JSON output.
  No login, cookies, or API keys required.
metadata:
  author: "mnbplus"
  license: "MIT-0"
  tags: ["resource-discovery", "download", "pan-links", "torrent", "ebook", "magnet"]
  openclaw:
    os: ["darwin", "linux", "windows"]
    requires:
      bins: ["python3"]
---

# Resource Hunter

## Overview

Public resource discovery engine for AI agents. Finds the best download routes across 15 sources (cloud drives, torrents, ebooks), ranks by quality, verifies link liveness, and returns structured JSON. Operates on public data only — no login, no DRM bypass.

## When to Use

- User wants to find a movie, TV show, anime, music, software, or book download
- User asks for pan links (阿里云盘, 夸克, 百度), magnets, or torrent results
- User wants to compare releases for quality (4K, 1080p, BluRay, REMUX, FLAC)
- User needs to probe or download a public video URL (Bilibili, YouTube, etc.)
- User wants subtitle files for a specific show or movie
- Another tool or script needs structured search results (`--json`)

## Do Not Use When

- The resource requires login, private account, cookies, or invite-only tracker
- The user is asking about DRM-protected, captcha-gated, or restricted content
- The task is about legality advice, copyright questions, or content moderation
- The user wants to access private libraries, seedboxes, or VPN-only trackers
- The question is a general knowledge question unrelated to resource discovery

## Quick Reference

```bash
# Locate skill directory
SKILL_DIR="$(openclaw skills path resource-hunter)/scripts"

# Search
python3 "$SKILL_DIR/hunt.py" search "Oppenheimer 2023" --4k --json
python3 "$SKILL_DIR/hunt.py" search "The Boys S05E03" --tv
python3 "$SKILL_DIR/hunt.py" search "Kamiina Botan" --anime
python3 "$SKILL_DIR/hunt.py" search "Jay Chou Fantasy FLAC" --music
python3 "$SKILL_DIR/hunt.py" search "Clean Code epub" --book
python3 "$SKILL_DIR/hunt.py" search "Adobe Photoshop 2024" --software --channel pan

# Skip pan probe (faster, may include dead links)
python3 "$SKILL_DIR/hunt.py" search "Interstellar 2014" --no-probe

# Video pipeline
python3 "$SKILL_DIR/hunt.py" video probe "https://www.bilibili.com/video/BV..."
python3 "$SKILL_DIR/hunt.py" video download "https://youtu.be/..." best

# Subtitles (user-initiated, not automatic)
python3 "$SKILL_DIR/hunt.py" subtitle "Breaking Bad" --season 1 --episode 1 --lang zh,en --json

# Diagnostics
python3 "$SKILL_DIR/hunt.py" sources --probe --json
python3 "$SKILL_DIR/hunt.py" doctor --json
python3 "$SKILL_DIR/hunt.py" cache stats --json
python3 "$SKILL_DIR/hunt.py" benchmark
```

## Procedure

Follow this exact order for every resource search request:

1. **Translate the query to English.** The engine only matches against English release titles. CJK titles will fail silently.
   - `黑袍纠察队` → `The Boys`
   - `梦魇绝镇` → `FROM`
   - `三体` → `3 Body Problem` or `Three Body Problem`
   - `上伊那ぼたん` → `Kamiina Botan`
   - `进击的巨人` → `Attack on Titan` or `Shingeki no Kyojin`

2. **Format the query using release naming conventions:**
   - TV: `{English Title} S{XX}E{XX}` → `The Boys S05E03`
   - Movie: `{English Title} {year}` → `Oppenheimer 2023`
   - Anime: `{Romanized Title}` → `Kamiina Botan` (short, how Nyaa indexes it)
   - Music: `{Artist} {Album} {format}` → `Jay Chou Fantasy FLAC`
   - Book: `{Title} {format}` → `Clean Code epub`
   - Software: `{Name} {version}` → `Adobe Photoshop 2024`

3. **Run the search** with the appropriate category flag:
   ```bash
   python3 "$SKILL_DIR/hunt.py" search "The Boys S05E03" --tv --json
   ```

4. **Interpret results** using these signals:
   - `tier`: `top` = high confidence, `related` = decent, `risky` = unreliable
   - `source_health.link_alive`: `true` = verified active, `false` = dead (skip it), `null` = unknown
   - `confidence`: 0.0–1.0 match score
   - `penalties`: if contains `"dead link detected"`, do not recommend
   - Book results from `annas` source: `link_or_magnet` is a detail page URL (user visits to choose download)

5. **Handle "No confident match"**: try alternative English titles or shortened names before reporting failure.

6. **For public video URLs**: skip search entirely, go straight to `video probe` or `video info`.

7. **For subtitle requests**: use `hunt.py subtitle` with `--season`, `--episode`, `--lang` flags.

## Source Routing

| Category | Primary → Fallback | Key Signal |
|:---------|:-------------------|:-----------|
| Movie | Pan → YTS/TPB → 1337x | Year in query |
| TV | EZTV/TPB → Pan | S{XX}E{XX} |
| Anime | Nyaa → Pan | Romanized title |
| Book | **Anna's Archive** → Pan → 1337x | Format (epub/pdf) |
| Music | Pan → Torrent (noise-filtered) | Lossless tags (FLAC) |
| Software | Pan → FitGirl/TorrentMac | Platform hint |
| Video URL | Skip search → `video probe` | URL pattern |

## Output Format

### Default (text)

Only `top` and `related` tiers are shown. Risky recall is suppressed.

### JSON v3 (`--json`)

```json
{
  "schema_version": "3",
  "query": "Oppenheimer 2023",
  "results": [
    {
      "tier": "top",
      "title": "Oppenheimer.2023.2160p.BluRay.REMUX",
      "link_or_magnet": "https://alipan.com/s/...",
      "provider": "aliyun",
      "source": "upyunso",
      "source_health": {
        "link_alive": true,
        "link_probe_reason": "share active"
      },
      "confidence": 0.95,
      "match_bucket": "exact_title_family",
      "canonical_identity": "movie:oppenheimer:2023"
    }
  ],
  "source_status": { "active": 15, "degraded": 0 }
}
```

| Field | Meaning |
|:------|:--------|
| `tier` | `top` = high confidence, `related` = decent, `risky` = suppressed |
| `source_health.link_alive` | `true` = verified, `false` = dead, `null` = unknown |
| `confidence` | 0.0–1.0 match confidence |
| `match_bucket` | `exact_title_family`, `title_family_match`, `weak_context_match`, etc. |
| `canonical_identity` | Deduplication key (e.g., `movie:oppenheimer:2023`) |

## Risks and Misuse

- **Searching with untranslated CJK titles.** The engine does NOT translate internally. Agent MUST translate first.
- **Recommending dead pan links.** Always check `source_health.link_alive` before presenting to user. Skip `false` results.
- **Triggering for login-only or private resources.** This skill only handles public routes. Do not attempt private trackers.
- **Using this skill for legal advice.** This skill finds routes, not legal opinions.
- **Overusing `--no-probe`.** Skipping the probe saves ~2s but may surface dead links. Only use when speed is critical.
- **Running video download without checking dependencies.** Always verify `yt-dlp` and `ffmpeg` before attempting downloads.

## Verification

After a search, verify success by checking:

1. At least one result with `tier: "top"` exists
2. The `canonical_identity` matches the user's intent (right movie/show/book)
3. For pan results: `source_health.link_alive` is `true` (not `false`)
4. For book results: the detail page URL is accessible

If no results pass these checks, try alternative English titles before reporting failure.

## Customization

When the user asks to add custom sources, change scoring, or set API keys:

- **All user changes go in `local/`** — this directory survives updates
- Custom source adapters: `local/sources/my_source.py` (auto-discovered on startup)
- Ranking weight overrides: `local/config.json`
- Environment variables: `local/.env` (overrides root `.env`)
- **Never modify files in `scripts/resource_hunter/`** for user-specific customizations
- Read `CONTRIBUTING.md` for the full adapter contract and examples

## Security & Privacy

- Does NOT execute arbitrary code from search results.
- Does NOT bypass DRM, captchas, or access controls.
- Does NOT require or store any login credentials, cookies, or API keys.
- All search queries go to public APIs and public websites only.
- Pan link probing uses anonymous share APIs — no account needed.
- Optional `curl-cffi` TLS impersonation is for public sites with bot detection, not for bypassing authentication.
- Cache is local SQLite only — no data is uploaded or shared.

## References

- Detailed usage: `references/usage.md`
- Internal architecture and JSON schema: `references/architecture.md`
- Source coverage and routing notes: `references/sources.md`
- Customization and development guide: `CONTRIBUTING.md`
