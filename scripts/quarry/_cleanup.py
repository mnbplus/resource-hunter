"""Auto-cleanup of deprecated files from previous versions.

Maintains a manifest of files that must not exist in the current
version.  On every startup ``purge_deprecated()`` silently removes
them so ZIP-update users never hit import conflicts.
"""
from __future__ import annotations

from pathlib import Path

_DEPRECATED: tuple[str, ...] = (
    # v1.1.0 — hunhepan removed, replaced by pansou and other pan aggregators
    "scripts/quarry/sources/hunhepan.py",
)


def purge_deprecated(project_root: Path | str | None = None) -> list[str]:
    """Remove deprecated files. Returns paths that were deleted."""
    if project_root is None:
        project_root = Path(__file__).resolve().parent.parent.parent

    root = Path(project_root)
    removed: list[str] = []

    for rel_path in _DEPRECATED:
        target = root / rel_path
        if target.is_file():
            try:
                target.unlink()
                removed.append(rel_path)
            except OSError:
                pass

    for rel_path in _DEPRECATED:
        if not rel_path.endswith(".py"):
            continue
        cache_dir = (root / rel_path).parent / "__pycache__"
        if cache_dir.is_dir():
            stem = Path(rel_path).stem
            for pyc in cache_dir.glob(f"{stem}.cpython-*.pyc"):
                try:
                    pyc.unlink()
                except OSError:
                    pass

    return removed
