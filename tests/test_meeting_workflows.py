from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError

import pytest

from agent_slack.server import API_VERSION, AgentSlackHTTPServer, _Handler
from agent_slack.servers import AgentServerManager


def _write_agent(project_root: Path, agent_id: str, title: str) -> None:
    path = project_root / ".claude" / "agents" / f"{agent_id}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\nname: {agent_id}\nsummary: {title}\n---\n# {title}\n\n{title}\n",
        encoding="utf-8",
    )


@contextmanager
def _running_server(tmp_path: Path):
    project_root = tmp_path / "agent-system"
    project_root.mkdir()
    _write_agent(project_root, "coordinator", "System Coordinator")
    _write_agent(project_root, "reviewer", "Review Agent")
    _write_agent(project_root, "implementer", "Implementation Agent")
    (project_root / ".agent-slack.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "orchestrators": [
                    {
                        "agent_id": "coordinator",
                        "default_participants": ["reviewer"],
                        "routes": [
                            {
                                "keywords": ["release"],
                                "participants": ["implementer"],
                            }
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    app_root = Path(__file__).resolve().parents[1]
    manager = AgentServerManager(app_root, tmp_path / "state", project_root)
    _Handler.manager = manager
    server = AgentSlackHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", manager.active_server_id
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _request(base_url: str, server_id: str, path: str, payload: dict | None = None):
    data = json.dumps(payload).encode() if payload is not None else None
    request = Request(
        f"{base_url}{path}",
        data=data,
        headers={
            "Content-Type": "application/json",
            "X-Agent-Slack-Server": server_id,
        },
        method="POST" if payload is not None else "GET",
    )
    with urlopen(request, timeout=5) as response:
        body = response.read().decode()
        if response.headers.get_content_type() == "application/x-ndjson":
            return [json.loads(line) for line in body.splitlines() if line]
        return json.loads(body)


def test_manual_group_meeting_is_listed_and_accepts_follow_up(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGENT_SLACK_CLI", "offline")
    with _running_server(tmp_path) as (base_url, server_id):
        chat = _request(
            base_url,
            server_id,
            "/api/chats",
            {
                "title": "Release Team",
                "member_ids": ["coordinator", "reviewer", "implementer"],
                "kind": "group",
            },
        )
        _request(base_url, server_id, f"/api/chats/{chat['chat_id']}/messages", {"body": "Review the release"})
        events = _request(
            base_url,
            server_id,
            f"/api/chats/{chat['chat_id']}/run-stream",
            {
                "mode": "meeting",
                "lead_agent_id": "coordinator",
                "participant_ids": ["coordinator", "reviewer", "implementer"],
                "objective": "Review the release",
            },
        )

        assert [event["agent_id"] for event in events if event["type"] == "agent_started"] == [
            "reviewer",
            "implementer",
            "coordinator",
        ]
        listed = _request(base_url, server_id, "/api/chats")["chats"]
        assert next(item for item in listed if item["chat_id"] == chat["chat_id"])["kind"] == "group"
        followed_up = _request(
            base_url,
            server_id,
            f"/api/chats/{chat['chat_id']}/messages",
            {"body": "What should the team do next?"},
        )
        assert followed_up["messages"][-1]["body"] == "What should the team do next?"


def test_auto_meeting_creates_sidebar_group_and_accepts_follow_up(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGENT_SLACK_CLI", "offline")
    with _running_server(tmp_path) as (base_url, server_id):
        direct = _request(
            base_url,
            server_id,
            "/api/chats",
            {"title": "System Coordinator", "member_ids": ["coordinator"], "kind": "direct"},
        )
        _request(
            base_url,
            server_id,
            f"/api/chats/{direct['chat_id']}/messages",
            {"body": "Coordinate a release review"},
        )
        events = _request(
            base_url,
            server_id,
            f"/api/chats/{direct['chat_id']}/run-stream",
            {
                "mode": "auto_meeting",
                "lead_agent_id": "coordinator",
                "objective": "Coordinate a release review",
            },
        )

        created = events[0]
        assert created["type"] == "meeting_created"
        assert created["chat_id"] != direct["chat_id"]
        assert created["agent_ids"] == ["coordinator", "implementer", "reviewer"]
        listed = _request(base_url, server_id, "/api/chats")["chats"]
        meeting_summary = next(item for item in listed if item["chat_id"] == created["chat_id"])
        assert meeting_summary["kind"] == "group"
        assert meeting_summary["member_ids"] == created["agent_ids"]

        meeting = _request(base_url, server_id, f"/api/chats/{created['chat_id']}")
        assert meeting["meetings"][-1]["status"] == "completed"
        assert meeting["messages"][1]["body"] == "Coordinate a release review"
        followed_up = _request(
            base_url,
            server_id,
            f"/api/chats/{created['chat_id']}/messages",
            {"body": "Please clarify the implementation risk"},
        )
        assert followed_up["messages"][-1]["author_type"] == "user"


def test_invalid_server_header_returns_json_404(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGENT_SLACK_CLI", "offline")
    with _running_server(tmp_path) as (base_url, _server_id):
        request = Request(
            f"{base_url}/api/agents",
            headers={"X-Agent-Slack-Server": "missing"},
        )
        with pytest.raises(HTTPError) as caught:
            urlopen(request, timeout=5)
        assert caught.value.code == 404
        assert json.loads(caught.value.read().decode()) == {"error": "Server not found: missing"}


def test_missing_active_folder_keeps_health_available(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGENT_SLACK_CLI", "offline")
    with _running_server(tmp_path) as (base_url, server_id):
        project_root = tmp_path / "agent-system"
        project_root.rename(tmp_path / "agent-system-offline")

        health = _request(base_url, server_id, "/api/health")

        assert health["ok"] is True
        assert health["architecture"] is None
        assert health["servers"][0]["available"] is False


def test_versioned_api_exposes_mobile_contract_and_preserves_legacy_routes(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGENT_SLACK_CLI", "offline")
    with _running_server(tmp_path) as (base_url, server_id):
        document = _request(base_url, server_id, "/api/v1")
        openapi = _request(base_url, server_id, "/api/v1/openapi.json")
        versioned_agents = _request(base_url, server_id, "/api/v1/agents")
        legacy_agents = _request(base_url, server_id, "/api/agents")

        assert document["service"] == "agent-slack"
        assert document["api_version"] == API_VERSION
        assert document["base_path"] == "/api/v1"
        assert openapi["openapi"] == "3.1.0"
        assert "/chats/{chat_id}/run-stream" in openapi["paths"]
        assert versioned_agents == legacy_agents


def test_versioned_mobile_chat_and_stream_workflow(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGENT_SLACK_CLI", "offline")
    with _running_server(tmp_path) as (base_url, server_id):
        chat = _request(
            base_url,
            server_id,
            "/api/v1/chats",
            {"title": "Mobile DM", "member_ids": ["reviewer"], "kind": "direct"},
        )
        _request(
            base_url,
            server_id,
            f"/api/v1/chats/{chat['chat_id']}/messages",
            {"body": "Reply from the background server"},
        )
        events = _request(
            base_url,
            server_id,
            f"/api/v1/chats/{chat['chat_id']}/run-stream",
            {"mode": "respond", "agent_ids": ["reviewer"]},
        )
        persisted = _request(base_url, server_id, f"/api/v1/chats/{chat['chat_id']}")

        assert events[0]["type"] == "run_started"
        assert events[-1]["type"] == "run_completed"
        assert persisted["messages"][-1]["author_type"] == "agent"


def test_api_responses_advertise_version_and_preflight_methods(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGENT_SLACK_CLI", "offline")
    with _running_server(tmp_path) as (base_url, server_id):
        request = Request(
            f"{base_url}/api/v1/health",
            headers={"X-Agent-Slack-Server": server_id},
        )
        with urlopen(request, timeout=5) as response:
            assert response.headers["X-Agent-Slack-Api-Version"] == API_VERSION

        preflight = Request(f"{base_url}/api/v1/chats", method="OPTIONS")
        with urlopen(preflight, timeout=5) as response:
            assert response.status == 204
            assert "POST" in response.headers["Access-Control-Allow-Methods"]
            assert response.headers["X-Agent-Slack-Api-Version"] == API_VERSION
