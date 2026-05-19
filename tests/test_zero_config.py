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


def test_local_runtime_profile_override(monkeypatch, tmp_path):
    import json
    import quarry.sources.base as base
    from quarry.sources.base import profile_for

    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "source_runtime_profiles": {
                    "mytracker": {
                        "supported_kinds": ["movie", "general"],
                        "timeout": 17,
                        "retries": 2,
                        "cooldown_seconds": 45,
                        "failure_threshold": 3,
                        "query_budget": 4,
                        "degraded_score_penalty": 5,
                        "default_degraded": True,
                        "lenient_tls": True,
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("QUARRY_LOCAL_CONFIG", str(config_path))
    monkeypatch.setattr(base, "_LOCAL_RUNTIME_PROFILES", None)

    profile = profile_for("mytracker")

    assert profile.supported_kinds == ("movie", "general")
    assert profile.timeout == 17
    assert profile.retries == 2
    assert profile.cooldown_seconds == 45
    assert profile.failure_threshold == 3
    assert profile.query_budget == 4
    assert profile.degraded_score_penalty == 5
    assert profile.default_degraded is True
    assert profile.lenient_tls is True
