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

    def fake_search(self, intent, plan=None, page=1, limit=8, use_cache=True, probe_links=True, max_sources=0):
        return fake_response

    monkeypatch.setattr("quarry.engine.ResourceHunterEngine.search", fake_search)
    rc = cli.main(["search", "test query", "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["query"] == "test query"
    assert payload["results"][0]["password"] == "1234"


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
