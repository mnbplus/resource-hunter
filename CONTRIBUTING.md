# Contributing to Resource Hunter

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
| Adding a custom source adapter | `local/sources/my_source.py` | ~~`scripts/resource_hunter/sources/`~~ |
| Overriding ranking weights | `local/config.json` | ~~editing `scripts/resource_hunter/config.py`~~ |
| Setting API keys, proxies, tokens | `local/.env` (or root `.env`) | ~~hardcoding in `.py` files~~ |
| Adding a custom subtitle source | `local/sources/my_subs.py` | ~~editing `scripts/resource_hunter/subdl.py`~~ |

### Why?

Files in `scripts/` are **project code** — they get overwritten on every update.  
Files in `local/` are **user code** — they are gitignored and never touched by updates.

The engine auto-discovers everything in `local/`:
- `local/sources/*.py` → auto-registered as source adapters on startup
- `local/config.json` → auto-loaded as ranking weight overrides
- `local/.env` → auto-loaded with priority over root `.env`

---

## How to write a custom source adapter

Create a file in `local/sources/` (e.g., `local/sources/my_tracker.py`):

```python
"""Custom source adapter — lives in local/ so it survives updates."""
from resource_hunter.sources.base import SourceAdapter, HTTPClient
from resource_hunter.models import SearchIntent, SearchResult


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

See `scripts/resource_hunter/config.py` for all available fields and their defaults.  
You only need to include the fields you want to override — defaults apply for the rest.

---

## How to add a runtime profile for a custom source

If your custom source needs specific timeout/retry/cooldown settings, add a profile entry to `local/config.json`:

> **Note**: Runtime profiles for custom sources are not yet auto-loaded from `local/config.json`. For now, the engine uses sensible defaults (timeout=10s, retries=1). This is planned for a future release.

---

## Rules for AI coding agents

When helping a user modify this project, follow these rules:

### 1. Identify the type of change

- **User-specific customization** (custom source, config tweak, API key) → `local/`
- **Bug fix or feature for the core project** → `scripts/resource_hunter/`
- **Not sure?** → Default to `local/`. It's always safe.

### 2. Never modify core files for user customizations

❌ **Wrong**: Editing `scripts/resource_hunter/sources/nyaa.py` to add a custom tracker  
✅ **Right**: Creating `local/sources/my_tracker.py` as a new adapter

❌ **Wrong**: Editing `scripts/resource_hunter/config.py` to change scoring weights  
✅ **Right**: Creating `local/config.json` with the override values

❌ **Wrong**: Hardcoding `HTTPS_PROXY=...` in a `.py` file  
✅ **Right**: Adding it to `local/.env`

### 3. Respect the adapter contract

Custom source adapters MUST:
- Subclass `SourceAdapter` from `resource_hunter.sources.base`
- Define `name`, `channel`, `priority` class attributes
- Implement `search()` returning `list[SearchResult]`
- Use the provided `http_client` for all HTTP requests (don't create your own)
- Never import private modules or access internal state

### 4. Don't modify these files unless fixing a bug

These are core project files with strict contracts:
- `scripts/resource_hunter/models.py` — data model contracts
- `scripts/resource_hunter/ranking.py` — scoring algorithm
- `scripts/resource_hunter/engine.py` — orchestration logic
- `scripts/resource_hunter/sources/base.py` — adapter base class

### 5. Test after changes

```bash
# Always run after making changes
python -m pytest tests/ -v

# Run benchmark to check search quality
python scripts/hunt.py benchmark
```

---

## Deprecated file management

When removing a file from the project, add its path to `_DEPRECATED` in `scripts/resource_hunter/_cleanup.py`:

```python
_DEPRECATED: tuple[str, ...] = (
    # ... existing entries ...
    "scripts/resource_hunter/old_module.py",   # v1.1.0 — removed because...
)
```

This ensures ZIP updaters get the file auto-cleaned on next startup.

---

## Project structure reference

```text
resource-hunter/
├── scripts/resource_hunter/     # 🔒 Core project code (updated by git pull / ZIP)
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
