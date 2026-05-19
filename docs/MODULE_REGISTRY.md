# MODULE_REGISTRY.md

This file records Quarry modules, their responsibilities, and dependency boundaries.
Update it whenever module structure, module responsibilities, public exports, or dependency relationships change.

## 1. Module Overview

| Module | Path | Type | Responsibility | Status |
|---|---|---|---|---|
| cli | `scripts/quarry/cli.py` | application module | Parse commands and route user-facing workflows | active |
| engine | `scripts/quarry/engine.py` | orchestration module | Coordinate intent parsing, source fan-out, scoring, probing, caching, and response assembly | active |
| source-validation | `scripts/quarry/source_validation.py` | tooling module | Validate custom SourceAdapter files against the public adapter contract | active |
| intent | `scripts/quarry/intent.py` | domain module | Parse queries, resolve aliases, and build category-specific search plans | active |
| ranking | `scripts/quarry/ranking.py` | domain module | Classify, score, tier, deduplicate, and diversify results | active |
| sources | `scripts/quarry/sources` | infrastructure module | Built-in and local source adapters, HTTP client, runtime profiles, mirror helpers | active |
| pan-probe | `scripts/quarry/pan_probe.py` | infrastructure module | Probe public cloud-drive share links and report alive/dead/unknown state | active |
| cache | `scripts/quarry/cache.py` | infrastructure module | SQLite-backed search cache, source health, video manifests, alias cache, history | active |
| parsing-utils | `scripts/quarry/parsers.py`, `scripts/quarry/text_utils.py`, `scripts/quarry/url_utils.py`, `scripts/quarry/common.py` | shared utility module | Text normalization, release tag parsing, provider detection, compatibility exports | active |
| video | `scripts/quarry/video_core.py` | workflow module | Public video metadata, download, subtitle extraction through yt-dlp | active |
| subtitles | `scripts/quarry/subdl.py`, `scripts/quarry/subhd.py`, `scripts/quarry/jimaku.py` | workflow module | User-initiated subtitle discovery and download | active |
| local-customization | `local` | user extension module | Update-safe custom sources, ranking overrides, runtime profile overrides, env overrides | active |
| docs | `README.md`, `README.zh-CN.md`, `SKILL.md`, `agents`, `references`, `docs` | documentation module | Human and agent-facing usage, architecture, source, and module boundary docs | active |
| tests | `tests` | test module | Unit, precision, CLI, benchmark, zero-config, and video regression tests | active |

## 2. Module Details

### cli

**Path:** `scripts/quarry/cli.py`

**Responsibilities:**
- Expose search, sources, doctor, benchmark, cache, history, video, and subtitle commands.
- Translate CLI flags into `SearchIntent` and workflow calls.
- Expose `source validate` for custom SourceAdapter contract checks.

**Public Exports:** `main`, `build_parser`

**Dependencies:** `engine`, `intent`, `rendering`, `cache`, `video`, `subtitles`, `source-validation`

**Import Rules:**
- Allowed: application entrypoints may import CLI.
- Forbidden: source adapters and shared utilities must not import CLI.

### engine

**Path:** `scripts/quarry/engine.py`

**Responsibilities:**
- Resolve aliases, build or consume search plans, run source adapters concurrently, score and probe results, write cache/history.
- Pass source runtime profile settings, including `lenient_tls`, into per-source HTTP clients.
- Use cached per-kind source health metrics to adapt source ordering and query budget conservatively.

**Public Exports:** `ResourceHunterEngine`, `build_plan`, `source_health`, `source_is_degraded`

**Dependencies:** `cache`, `intent`, `models`, `ranking`, `sources`, `pan-probe`, `benchmark`

**Import Rules:**
- Allowed: CLI and tests may instantiate `ResourceHunterEngine`.
- Forbidden: source adapters must not import engine.

### source-validation

**Path:** `scripts/quarry/source_validation.py`

**Responsibilities:**
- Load a custom source file and find `SourceAdapter` subclasses.
- Validate `name`, `channel`, `priority`, `search()` signature, smoke-test return type, and required result fields.
- Report adapter validity as text or JSON through the CLI.

**Public Exports:** `validate_source_file`

**Dependencies:** `models`, `sources.base`, standard-library import and inspection helpers

**Import Rules:**
- Allowed: CLI and tests may call `validate_source_file`.
- Forbidden: validator must not register adapters globally or mutate source runtime configuration.

### intent

**Path:** `scripts/quarry/intent.py`

**Responsibilities:**
- Parse query kind, channel, quality, episode, format, version, and language hints.
- Run best-effort public metadata alias resolution for eligible CJK movie/TV/anime/general queries.
- Build category-specific pan/torrent query families and source preferences.

**Public Exports:** `AliasResolver`, `parse_intent`, `enrich_intent_with_aliases`, `build_plan`

**Dependencies:** `cache`, `common`, `models`, `sources.HTTPClient`

**Import Rules:**
- Allowed: engine and tests may use public exports.
- Forbidden: intent must not depend on ranking or concrete source adapters.

### ranking

**Path:** `scripts/quarry/ranking.py`

**Responsibilities:**
- Classify result/title match buckets.
- Apply configurable scoring, source health penalties, deduplication, sorting, and diversity.

**Public Exports:** `classify_result`, `score_result`, `deduplicate_results`, `diversify_results`, `source_health`, `source_is_degraded`

**Dependencies:** `cache`, `common`, `config`, `models`, `sources.profile_for`

**Import Rules:**
- Allowed: engine and tests may call public scoring helpers.
- Forbidden: ranking must not call source adapter search methods.

### sources

**Path:** `scripts/quarry/sources`

**Responsibilities:**
- Define `SourceAdapter`, `HTTPClient`, `BrowserClient`, `SourceRuntimeProfile`, and source helper functions.
- Register built-in source adapters and auto-discover `local/sources/*.py`.
- Load `local/config.json` `source_runtime_profiles` overrides.

**Public Exports:** `SourceAdapter`, `HTTPClient`, `SourceRegistry`, `default_adapters`, `profile_for`, concrete source classes

**Dependencies:** `models`, `common`, `mirror_health`, optional `httpx`, optional `curl-cffi`, optional Playwright

**Import Rules:**
- Allowed: custom sources may import `SourceAdapter`, `HTTPClient`, and public models.
- Forbidden: custom sources should not import private engine/ranking internals.

### pan-probe

**Path:** `scripts/quarry/pan_probe.py`

**Responsibilities:**
- Probe supported cloud-drive providers anonymously.
- Return `alive=True` only for explicit positive share signals, `alive=False` for explicit dead signals, and `alive=None` for ambiguous pages.

**Public Exports:** `PanLinkProber`, `ProbeResult`

**Dependencies:** standard-library HTTP/JSON parsing only

**Import Rules:**
- Allowed: engine may use probe results to annotate and demote dead pan links.
- Forbidden: pan-probe must not depend on ranking or source adapters.

### cache

**Path:** `scripts/quarry/cache.py`

**Responsibilities:**
- Maintain local SQLite cache tables for searches, source health, video manifests, alias resolution, and history.
- Provide circuit-breaker source skip decisions.
- Store source result metrics for adaptive scheduling and `doctor --json` health output.

**Public Exports:** `ResourceCache`

**Dependencies:** `common.storage_root`, `models.SourceStatus`

**Import Rules:**
- Allowed: engine, ranking, intent, video workflows may use cache.
- Forbidden: cache must not import source adapters or CLI.

### local-customization

**Path:** `local`

**Responsibilities:**
- Hold update-safe user extensions and local configuration.
- `local/sources/*.py` adds custom source adapters.
- `local/config.json` overrides ranking weights and source runtime profiles.
- `local/.env` overrides environment variables.

**Public Exports:** discovered at runtime through `SourceRegistry` and `profile_for`

**Dependencies:** public `quarry.sources.base` and `quarry.models` APIs

**Import Rules:**
- Allowed: local sources may import public adapter and model contracts.
- Forbidden: core project updates should not overwrite user local files.

## 3. Dependency Graph

```txt
hunt.py
  `-- cli
      |-- engine
      |   |-- intent
      |   |   |-- cache
      |   |   |-- models
      |   |   `-- sources.HTTPClient
      |   |-- sources
      |   |   |-- models
      |   |   |-- parsing-utils
      |   |   `-- local-customization
      |   |-- ranking
      |   |   |-- cache
      |   |   |-- config
      |   |   `-- sources.profile_for
      |   |-- pan-probe
      |   `-- cache
      |-- video
      |-- subtitles
      `-- source-validation
```

No circular dependency is intentionally allowed.

## 4. Module Boundary Rules

- `sources` adapters return `SearchResult` objects and must not call engine orchestration.
- `ranking` scores normalized results and must not perform network requests.
- `intent` may use public metadata lookup but must not rank results.
- `local/` is the only safe place for user-specific source adapters, config, tokens, and proxies.
- `source-validation` may inspect custom adapter files, but must not auto-enable failing adapters.
- Default TLS is strict; only a source runtime profile may opt into `lenient_tls`.
- Pan probe can demote explicit dead links, but ambiguous links must remain unknown rather than false-positive alive.

## 5. New Module Registration Template

### Module Name

**Path:**

```txt
scripts/quarry/module-name
```

**Responsibilities:**
- ...

**Out of Scope:**
- ...

**Public Exports:**

```python
from quarry.module_name import ...
```

**Dependencies:**

| Dependency | Purpose |
|---|---|
| ... | ... |

**Used By:**

| Consumer | Purpose |
|---|---|
| ... | ... |

**Import Rules:**
- Allowed:
- Forbidden:

**Maintenance Notes:**
- ...
