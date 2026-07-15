from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.request import Request, urlopen

from agent_slack.server import _Handler
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
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
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
