# Contributing to Quarry

> **This guide is for both human developers and AI coding agents.**  
> If you are an AI assistant helping a user customize this project, **read the rules below before making any changes.**

---

## Golden Rule

**User customizations go in `local/`. Project code goes in `scripts/`.**

Never mix them. This separation ensures updates (`git pull` / ZIP) never destroy user work.

---

## Where to put things

| What you're doing | Where to put it | ⚠️ Do NOT put it here |
|:-------------------|:----------------|:----------------------|
| Adding a custom source adapter | `local/sources/my_source.py` | ~~`scripts/quarry/sources/`~~ |
| Overriding ranking weights | `local/config.json` | ~~editing `scripts/quarry/config.py`~~ |
| Setting API keys, proxies, tokens | `local/.env` (or root `.env`) | ~~hardcoding in `.py` files~~ |
| Adding a custom subtitle source | `local/sources/my_subs.py` | ~~editing `scripts/quarry/subdl.py`~~ |

### Why?

Files in `scripts/` are **project code** — they get overwritten on every update.  
Files in `local/` are **user code** — they are gitignored and never touched by updates.

The engine auto-discovers everything in `local/`:
- `local/sources/*.py` → auto-registered as source adapters on startup
- `local/config.json` → auto-loaded as ranking weight and source runtime profile overrides
- `local/.env` → auto-loaded with priority over root `.env`

---

## How to write a custom source adapter

Create a file in `local/sources/` (e.g., `local/sources/my_tracker.py`):

```python
"""Custom source adapter — lives in local/ so it survives updates."""
from quarry.sources.base import SourceAdapter, HTTPClient
from quarry.models import SearchIntent, SearchResult


class MyTrackerSource(SourceAdapter):
    name = "mytracker"          # unique identifier
    channel = "torrent"         # "pan" or "torrent"
    priority = 3                # 1 = highest priority

    def search(self, query, intent, limit, page, http_client):
        # Use http_client.get_text() / get_json() for HTTP requests
        # Return a list of SearchResult objects
        return []
```

That's it. The engine will auto-discover and register it on next startup.

Validate the adapter before relying on it:

```bash
python scripts/hunt.py source validate local/sources/my_tracker.py --json
```

Use `--no-smoke` if the source cannot safely run a lightweight `search()` smoke test during validation.

### Required fields

| Field | Type | Description |
|:------|:-----|:------------|
| `name` | `str` | Unique source identifier (lowercase, no spaces) |
| `channel` | `str` | `"pan"` or `"torrent"` |
| `priority` | `int` | Lower = higher priority in routing |

### Required method

```python
def search(self, query: str, intent: SearchIntent, limit: int, page: int, http_client: HTTPClient) -> list[SearchResult]:
```

### Optional method

```python
def healthcheck(self, http_client: HTTPClient) -> tuple[bool, str]:
    """Return (ok, error_message). Called by `hunt.py sources --probe`."""
```

---

## How to override ranking weights

Create `local/config.json`:

```json
{
  "resolution_4k_bonus": 25,
  "lossless_bonus": 20,
  "cam_penalty": -40,
  "pan_provider_scores": {
    "aliyun": 15,
    "quark": 14,
    "baidu": 5
  }
}
```

See `scripts/quarry/config.py` for all available fields and their defaults.  
You only need to include the fields you want to override — defaults apply for the rest.

---

## How to add a runtime profile for a custom source

If your custom source needs specific timeout/retry/cooldown settings, add a profile entry to `local/config.json`:

```json
{
  "source_runtime_profiles": {
    "mytracker": {
      "supported_kinds": ["movie", "tv", "general"],
      "timeout": 12,
      "retries": 1,
      "cooldown_seconds": 180,
      "failure_threshold": 2,
      "query_budget": 2,
      "degraded_score_penalty": 4,
      "default_degraded": false,
      "lenient_tls": false
    }
  }
}
```

Use `lenient_tls: true` only for a source with known non-standard TLS behavior. The default is strict certificate validation.

---

## Rules for AI coding agents

When helping a user modify this project, follow these rules:

### 1. Identify the type of change

- **User-specific customization** (custom source, config tweak, API key) → `local/`
- **Bug fix or feature for the core project** → `scripts/quarry/`
- **Not sure?** → Default to `local/`. It's always safe.

### 2. Never modify core files for user customizations

❌ **Wrong**: Editing `scripts/quarry/sources/nyaa.py` to add a custom tracker  
✅ **Right**: Creating `local/sources/my_tracker.py` as a new adapter

❌ **Wrong**: Editing `scripts/quarry/config.py` to change scoring weights  
✅ **Right**: Creating `local/config.json` with the override values

❌ **Wrong**: Hardcoding `HTTPS_PROXY=...` in a `.py` file  
✅ **Right**: Adding it to `local/.env`

### 3. Respect the adapter contract

Custom source adapters MUST:
- Subclass `SourceAdapter` from `quarry.sources.base`
- Define `name`, `channel`, `priority` class attributes
- Implement `search()` returning `list[SearchResult]`
- Use the provided `http_client` for all HTTP requests (don't create your own)
- Never import private modules or access internal state
- Pass `python scripts/hunt.py source validate local/sources/my_source.py --json`

### 4. Don't modify these files unless fixing a bug

These are core project files with strict contracts:
- `scripts/quarry/models.py` — data model contracts
- `scripts/quarry/ranking.py` — scoring algorithm
- `scripts/quarry/engine.py` — orchestration logic
- `scripts/quarry/sources/base.py` — adapter base class

### 5. Test after changes

```bash
# Always run after making changes
python -m pytest tests/ -v

# Run benchmark to check search quality
python scripts/hunt.py benchmark
```

---

## Deprecated file management

When removing a file from the project, add its path to `_DEPRECATED` in `scripts/quarry/_cleanup.py`:

```python
_DEPRECATED: tuple[str, ...] = (
    # ... existing entries ...
    "scripts/quarry/old_module.py",   # v1.1.0 — removed because...
)
```

This ensures ZIP updaters get the file auto-cleaned on next startup.

---

## Project structure reference

```text
quarry/
├── scripts/quarry/     # 🔒 Core project code (updated by git pull / ZIP)
│   ├── sources/                 # Built-in source adapters
│   ├── engine.py                # Search orchestration
│   ├── ranking.py               # Scoring algorithm
│   └── ...
├── local/                       # 🛡️ User safe zone (NEVER touched by updates)
│   ├── sources/                 # Custom source adapters
│   ├── config.json              # Ranking overrides
│   └── .env                     # Environment overrides
├── agents/                      # Agent skill configs (hermes.yaml, openclaw.yaml)
├── tests/                       # Test suite
└── references/                  # Architecture and usage docs
```
