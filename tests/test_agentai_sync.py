from __future__ import annotations

import json

import agentai_sync


class _FakeResponse:
    def __init__(self, payload: dict):
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return json.dumps(self._payload).encode("utf-8")


def test_is_agentai_sync_ready_requires_required_fields():
    ready, message = agentai_sync.is_agentai_sync_ready(
        {
            "agentai_sync": {
                "enabled": True,
                "api_url": "",
                "token": "",
            }
        }
    )

    assert ready is False
    assert "API URL" in message


def test_publish_integration_snapshot_posts_snapshot(monkeypatch, tmp_path):
    captured = {}

    def fake_urlopen(req, timeout=0):
        captured["url"] = req.full_url
        captured["headers"] = dict(req.header_items())
        captured["body"] = json.loads(req.data.decode("utf-8"))
        captured["timeout"] = timeout
        return _FakeResponse({"status": "ok"})

    monkeypatch.setattr(
        agentai_sync,
        "build_integration_snapshot",
        lambda **kwargs: {
            "generated_at": "2026-04-07T10:00:00+00:00",
            "summary": {"download_gap_count": 1},
            "latest_downloads": [],
            "latest_qb_sync": [],
            "latest_qb_attempts": [],
            "ai_suggestions": [],
            "world_clocks": [],
        },
    )
    monkeypatch.setattr(agentai_sync.request, "urlopen", fake_urlopen)

    result = agentai_sync.publish_integration_snapshot(
        base_dir=tmp_path,
        config={
            "agentai_sync": {
                "enabled": True,
                "api_url": "https://agentai.example.com",
                "token": "secret-token",
                "machine_id": "stockton-frontdesk-01",
                "machine_name": "Stockton Frontdesk",
            }
        },
    )

    assert result["ok"] is True
    assert captured["url"] == "https://agentai.example.com/edge/projects/integration-full/snapshot"
    assert captured["body"]["machine_id"] == "stockton-frontdesk-01"
    assert captured["body"]["snapshot"]["summary"]["download_gap_count"] == 1
    assert captured["headers"]["X-agentai-token"] == "secret-token"


def test_fetch_and_report_agentai_command(monkeypatch):
    responses = [
        {"command": {"id": "cmd-1", "command_type": "download_missing_reports", "payload": {"store": "Stockton"}}},
        {"status": "ok"},
    ]
    captured = []

    def fake_urlopen(req, timeout=0):
        captured.append((req.full_url, req.get_method(), json.loads(req.data.decode("utf-8")) if req.data else None))
        return _FakeResponse(responses[len(captured) - 1])

    monkeypatch.setattr(agentai_sync.request, "urlopen", fake_urlopen)

    config = {
        "agentai_sync": {
            "enabled": True,
            "api_url": "https://agentai.example.com",
            "token": "secret-token",
            "machine_id": "stockton-frontdesk-01",
            "machine_name": "Stockton Frontdesk",
        }
    }

    polled = agentai_sync.fetch_next_agentai_command(config=config)
    reported = agentai_sync.report_agentai_command_result(
        "cmd-1",
        status="success",
        result={"success": 2},
        config=config,
    )

    assert polled["ok"] is True
    assert polled["command"]["id"] == "cmd-1"
    assert reported["ok"] is True
    assert captured[0][0] == "https://agentai.example.com/edge/projects/integration-full/commands/stockton-frontdesk-01"
    assert captured[0][1] == "GET"
    assert captured[1][0] == "https://agentai.example.com/edge/commands/cmd-1/result"
    assert captured[1][2]["status"] == "success"
