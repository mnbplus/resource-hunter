# Local Customization Directory

This directory is your **safe zone** for custom configurations. It is:

- ✅ **Gitignored** — never tracked by version control
- ✅ **Update-safe** — never overwritten by `git pull` or ZIP updates
- ✅ **Auto-loaded** — the engine discovers and loads everything here on startup

## Directory Structure

```
local/
├── sources/          # Custom source adapters (auto-registered)
│   └── my_source.py  # Your custom SourceAdapter subclass
├── config.json       # Ranking weight overrides
└── .env              # (alternative location for environment variables)
```

## Custom Source Adapters

Drop any `.py` file into `local/sources/` that defines a `SourceAdapter` subclass.
The engine auto-discovers and registers it on startup.

Example `local/sources/my_tracker.py`:

```python
from quarry.sources.base import SourceAdapter, HTTPClient
from quarry.models import SearchIntent, SearchResult

class MyTrackerSource(SourceAdapter):
    name = "mytracker"
    channel = "torrent"
    priority = 3

    def search(self, query, intent, limit, page, http_client):
        # Your custom search logic here
        return []
```

Validate your adapter before using it in normal searches:

```bash
python scripts/hunt.py source validate local/sources/my_tracker.py --json
```

The validator checks the adapter class, `name`/`channel`/`priority`, `search()` signature, smoke-test return type, required result fields, and whether returned results used the provided `http_client`.

## Config Overrides

Create `local/config.json` to override ranking weights and source runtime profiles without editing source code:

```json
{
  "resolution_4k_bonus": 25,
  "lossless_bonus": 20,
  "pan_provider_scores": {
    "aliyun": 15,
    "quark": 14
  },
  "source_runtime_profiles": {
    "mytracker": {
      "supported_kinds": ["movie", "tv", "general"],
      "timeout": 12,
      "retries": 1,
      "cooldown_seconds": 180,
      "failure_threshold": 2,
      "query_budget": 2,
      "degraded_score_penalty": 4,
      "lenient_tls": false
    }
  }
}
```

Set `lenient_tls` to `true` only for a source with known non-standard TLS behavior.

## Environment Variables

You can also place a `.env` file here (in addition to the project root `.env`).
Local `.env` values take priority over root `.env`.
