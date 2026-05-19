#!/usr/bin/env python3
import os
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

# Auto-load .env for background agent compatibility (proxies, tokens)
for _env_candidate in (SCRIPT_DIR.parent / ".env", SCRIPT_DIR.parent / "local" / ".env"):
    if _env_candidate.is_file():
        _env_text = ""
        for _enc in ("utf-8", "utf-8-sig", "utf-16", "utf-16-le", "latin-1"):
            try:
                _env_text = _env_candidate.read_text(encoding=_enc)
                break
            except (UnicodeDecodeError, UnicodeError):
                continue
        for _line in _env_text.splitlines():
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _v = _line.split("=", 1)
                # local/.env overrides root .env (uses os.environ[] not setdefault)
                if "local" in str(_env_candidate):
                    os.environ[_k.strip()] = _v.strip()
                else:
                    os.environ.setdefault(_k.strip(), _v.strip())

# Auto-cleanup deprecated files from previous versions (safe for ZIP updaters)
from quarry._cleanup import purge_deprecated

_removed = purge_deprecated()
if _removed:
    print(f"Cleaned {len(_removed)} deprecated file(s) from a previous version:", file=sys.stderr)
    for _f in _removed:
        print(f"   removed: {_f}", file=sys.stderr)

from quarry.cli import main


if __name__ == "__main__":
    raise SystemExit(main())
