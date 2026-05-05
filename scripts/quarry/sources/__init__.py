"""Plugin-based source adapter registry.

Each source adapter lives in its own file under ``sources/``.
The registry auto-discovers them via direct imports below.

Usage::

    from quarry.sources import default_adapters
    pan_sources, torrent_sources = default_adapters()

    # Or via registry:
    from quarry.sources import SourceRegistry
    registry = SourceRegistry()
    for adapter in registry.pan_adapters():
        ...
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .base import (
    DEFAULT_HEADERS,
    HTTPClient,
    SOURCE_RUNTIME_PROFILES,
    SourceAdapter,
    SourceRuntimeProfile,
    TRACKERS,
    _clean_magnet,
    _flatten_pan_payload,
    _format_size,
    _make_magnet,
    _validate_pan_payload,
    profile_for,
)

# Import all concrete adapters — they self-register via SourceRegistry
from .ps252035 import Ps252035Source
from .panhunt import PanhuntSource
from .upyunso import UpyunsoSource
from .tpb import TPBSource
from .nyaa import NyaaSource
from .eztv import EZTVSource
from .yts import YTSSource
from .x1337 import OneThreeThreeSevenXSource
from .limetorrents import LimeTorrentsSource
from .fitgirl import FitGirlSource
from .torznab import TorznabSource
from .bitsearch import BitsearchSource
from .torrentmac import TorrentMacSource
from .annas import AnnasArchiveSource
from .pansou import PanSouSource
from .dmhy import DMHYSource
from .bangumi_moe import BangumiMoeSource
from .torrentgalaxy import TorrentGalaxySource
from .torlock import TorLockSource
from .ext_to import ExtToSource
from .subsplease import SubsPleaseSource
from .knaben import KnabenSource
from .btdig import BTDiggSource
from .solidtorrents import SolidTorrentsSource
from .libgen import LibgenSource
from .torrentcsv import TorrentCSVSource
from .glodls import GLODLSSource
from .idope import IDOPESource


class SourceRegistry:
    """Central registry for all source adapters.

    Built-in adapters are registered via ``register_defaults()``.
    User-defined adapters in ``local/sources/*.py`` are auto-discovered
    and appended — they are never overwritten by project updates.
    """

    def __init__(self) -> None:
        self._pan: list[SourceAdapter] = []
        self._torrent: list[SourceAdapter] = []
        self.register_defaults()
        self._load_local_sources()

    def register(self, adapter: SourceAdapter) -> None:
        if adapter.channel == "pan":
            self._pan.append(adapter)
        else:
            self._torrent.append(adapter)

    def register_defaults(self) -> None:
        from .upyunso import _HAS_CRYPTO
        import logging
        _logger = logging.getLogger(__name__)
        if _HAS_CRYPTO:
            self._pan = [UpyunsoSource(), PanSouSource(), Ps252035Source(), PanhuntSource()]
        else:
            _logger.warning("upyunso disabled: pycryptodome / pycryptodomex not installed (pip install pycryptodome)")
            self._pan = [PanSouSource(), Ps252035Source(), PanhuntSource()]
        self._torrent = [TorznabSource(), NyaaSource(), DMHYSource(), BangumiMoeSource(), SubsPleaseSource(), EZTVSource(), TorrentGalaxySource(), BitsearchSource(), TPBSource(), YTSSource(), OneThreeThreeSevenXSource(), LimeTorrentsSource(), TorLockSource(), FitGirlSource(), TorrentMacSource(), ExtToSource(), AnnasArchiveSource(), KnabenSource(), BTDiggSource(), SolidTorrentsSource(), LibgenSource(), TorrentCSVSource(), GLODLSSource(), IDOPESource()]

    def _load_local_sources(self) -> None:
        """Auto-discover custom source adapters from ``local/sources/``."""
        import importlib.util
        import sys

        # project root = scripts/../ → up two levels from this file
        local_dir = Path(__file__).resolve().parent.parent.parent.parent / "local" / "sources"
        if not local_dir.is_dir():
            return

        existing_names = set(self.names())
        for py_file in sorted(local_dir.glob("*.py")):
            if py_file.name.startswith("_"):
                continue
            module_name = f"_local_source_{py_file.stem}"
            try:
                spec = importlib.util.spec_from_file_location(module_name, str(py_file))
                if spec is None or spec.loader is None:
                    continue
                mod = importlib.util.module_from_spec(spec)
                sys.modules[module_name] = mod
                spec.loader.exec_module(mod)
                # Find all SourceAdapter subclasses defined in the module
                for attr_name in dir(mod):
                    obj = getattr(mod, attr_name)
                    if (isinstance(obj, type)
                            and issubclass(obj, SourceAdapter)
                            and obj is not SourceAdapter
                            and hasattr(obj, "name")
                            and obj.name not in existing_names):
                        instance = obj()
                        self.register(instance)
                        existing_names.add(obj.name)
            except Exception:
                pass  # don't crash on broken user plugins

    def pan_adapters(self) -> list[SourceAdapter]:
        return list(self._pan)

    def torrent_adapters(self) -> list[SourceAdapter]:
        return list(self._torrent)

    def all_adapters(self) -> list[SourceAdapter]:
        return self._pan + self._torrent

    def get(self, name: str) -> SourceAdapter | None:
        for adapter in self.all_adapters():
            if adapter.name == name:
                return adapter
        return None

    def names(self) -> list[str]:
        return [a.name for a in self.all_adapters()]


def default_adapters() -> tuple[list[SourceAdapter], list[SourceAdapter]]:
    """Return (pan_sources, torrent_sources) with default configuration."""
    registry = SourceRegistry()
    return registry.pan_adapters(), registry.torrent_adapters()


__all__ = [
    "AnnasArchiveSource",
    "BTDiggSource",
    "BangumiMoeSource",
    "BitsearchSource",
    "DEFAULT_HEADERS",
    "DMHYSource",
    "EZTVSource",
    "ExtToSource",
    "FitGirlSource",
    "GLODLSSource",
    "HTTPClient",
    "IDOPESource",
    "KnabenSource",
    "LibgenSource",
    "LimeTorrentsSource",
    "NyaaSource",
    "OneThreeThreeSevenXSource",
    "PanSouSource",
    "PanhuntSource",
    "Ps252035Source",
    "SOURCE_RUNTIME_PROFILES",
    "SolidTorrentsSource",
    "SourceAdapter",
    "SourceRegistry",
    "SourceRuntimeProfile",
    "SubsPleaseSource",
    "TPBSource",
    "TRACKERS",
    "TorLockSource",
    "TorrentCSVSource",
    "TorrentGalaxySource",
    "TorznabSource",
    "TorrentMacSource",
    "UpyunsoSource",
    "YTSSource",
    "_clean_magnet",
    "_flatten_pan_payload",
    "_format_size",
    "_make_magnet",
    "_validate_pan_payload",
    "default_adapters",
    "profile_for",
]
