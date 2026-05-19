from __future__ import annotations

import json

from quarry import cli


def test_cli_search_json(monkeypatch, capsys):
    fake_response = {
        "query": "test query",
        "intent": {"kind": "general", "quick": False},
        "plan": {"channels": ["pan", "torrent"], "notes": ["demo"]},
        "results": [
            {
                "channel": "pan",
                "source": "2fun",
                "provider": "aliyun",
                "title": "Demo",
                "link_or_magnet": "https://example.com",
                "password": "1234",
                "share_id_or_info_hash": "abc",
                "size": "",
                "seeders": 0,
                "quality": "",
                "score": 77,
                "reasons": ["query match"],
                "raw": {},
            }
        ],
        "warnings": [],
        "source_status": [],
        "meta": {"cached": False},
    }

    def fake_search(self, intent, plan=None, page=1, limit=8, use_cache=True, probe_links=True, max_sources=0, explain=False):
        if explain:
            fake_response["explain"] = {"why_top": ["query match"], "why_not_others": []}
        return fake_response

    monkeypatch.setattr("quarry.engine.ResourceHunterEngine.search", fake_search)
    rc = cli.main(["search", "test query", "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["query"] == "test query"
    assert payload["results"][0]["password"] == "1234"

    rc = cli.main(["search", "test query", "--json", "--explain"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["explain"]["why_top"] == ["query match"]


def test_cli_sources_text(monkeypatch, capsys):
    def fake_catalog(self, probe=False):
        return {
            "sources": [
                {
                    "source": "2fun",
                    "channel": "pan",
                    "priority": 1,
                    "recent_status": {"ok": True, "skipped": False, "latency_ms": 42, "error": "", "checked_at": "now"},
                }
            ],
            "meta": {"probe": probe},
        }

    monkeypatch.setattr("quarry.engine.ResourceHunterEngine.source_catalog", fake_catalog)
    rc = cli.main(["sources"])
    assert rc == 0
    output = capsys.readouterr().out
    assert "2fun" in output
    assert "priority=1" in output


def test_source_validate_accepts_adapter(tmp_path, capsys):
    source_file = tmp_path / "my_source.py"
    source_file.write_text(
        """
from quarry.sources.base import SourceAdapter
from quarry.models import SearchResult

class MySource(SourceAdapter):
    name = "my_source"
    channel = "torrent"
    priority = 3

    def search(self, query, intent, limit, page, http_client):
        http_client.get_text("https://example.com")
        return [SearchResult(channel="torrent", source=self.name, provider="magnet", title="Ubuntu", link_or_magnet="magnet:?xt=urn:btih:abc")]
""",
        encoding="utf-8",
    )

    rc = cli.main(["source", "validate", str(source_file), "--json"])

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["valid"] is True
    assert payload["adapters"][0]["name"] == "my_source"


def test_source_validate_rejects_missing_adapter(tmp_path, capsys):
    source_file = tmp_path / "empty.py"
    source_file.write_text("VALUE = 1\n", encoding="utf-8")

    rc = cli.main(["source", "validate", str(source_file), "--json"])

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["valid"] is False
    assert "no SourceAdapter subclass found" in payload["errors"]


def test_source_validate_rejects_smoke_failure(tmp_path, capsys):
    source_file = tmp_path / "broken_search.py"
    source_file.write_text(
        """
from quarry.sources.base import SourceAdapter

class BrokenSearchSource(SourceAdapter):
    name = "broken_search"
    channel = "torrent"
    priority = 3

    def search(self, query, intent, limit, page, http_client):
        raise RuntimeError("boom")
""",
        encoding="utf-8",
    )

    rc = cli.main(["source", "validate", str(source_file), "--json"])

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["valid"] is False
    assert payload["adapters"][0]["errors"] == ["search smoke test failed: boom"]


def test_source_validate_rejects_init_failure_as_json(tmp_path, capsys):
    source_file = tmp_path / "broken_init.py"
    source_file.write_text(
        """
from quarry.sources.base import SourceAdapter

class BrokenInitSource(SourceAdapter):
    name = "broken_init"
    channel = "torrent"
    priority = 3

    def __init__(self):
        raise RuntimeError("init boom")

    def search(self, query, intent, limit, page, http_client):
        return []
""",
        encoding="utf-8",
    )

    rc = cli.main(["source", "validate", str(source_file), "--json"])

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["valid"] is False
    assert payload["adapters"][0]["errors"] == ["adapter initialization failed: init boom"]
