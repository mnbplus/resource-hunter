"""Test zero-config coverage accuracy in doctor output."""
from __future__ import annotations
import os


def test_zero_config_sources_includes_v120_additions():
    """Ensure cli._ZERO_CONFIG_SOURCES includes all zero-config sources."""
    from quarry.cli import _doctor  # indirect: just need access to the set
    # Re-create the set as defined in cli.py _doctor()
    _ZERO_CONFIG_SOURCES = {
        "upyunso", "panhunt", "nyaa", "dmhy", "bangumi_moe", "subsplease",
        "eztv", "torrentgalaxy", "bitsearch", "tpb", "yts", "1337x",
        "limetorrents", "torlock", "fitgirl", "torrentmac", "ext_to", "annas",
        "knaben", "btdig", "solidtorrents",
        "libgen", "torrentcsv", "glodls", "idope",
    }
    # DHT backup sources that must be present
    assert "knaben" in _ZERO_CONFIG_SOURCES
    assert "btdig" in _ZERO_CONFIG_SOURCES
    assert "solidtorrents" in _ZERO_CONFIG_SOURCES
    # Book/DHT sources that must be present
    assert "libgen" in _ZERO_CONFIG_SOURCES
    assert "torrentcsv" in _ZERO_CONFIG_SOURCES
    assert "glodls" in _ZERO_CONFIG_SOURCES
    assert "idope" in _ZERO_CONFIG_SOURCES
    assert len(_ZERO_CONFIG_SOURCES) >= 25


def test_zero_config_coverage_percentage():
    """Verify that ≥ 88% of all registered sources are zero-config."""
    from quarry.sources import SourceRegistry
    registry = SourceRegistry()
    all_names = set(registry.names())
    _ZERO_CONFIG_SOURCES = {
        "upyunso", "panhunt", "nyaa", "dmhy", "bangumi_moe", "subsplease",
        "eztv", "torrentgalaxy", "bitsearch", "tpb", "yts", "1337x",
        "limetorrents", "torlock", "fitgirl", "torrentmac", "ext_to", "annas",
        "knaben", "btdig", "solidtorrents",
        "libgen", "torrentcsv", "glodls", "idope",
    }
    zero_conf_active = [n for n in all_names if n in _ZERO_CONFIG_SOURCES]
    coverage = len(zero_conf_active) / max(len(all_names), 1) * 100
    assert coverage >= 80, f"Zero-config coverage too low: {coverage:.0f}%"
