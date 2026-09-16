from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from test_meeting_workflows import _request, _running_server


def _post(base_url: str, server_id: str, path: str, payload: dict, request_id: str | None = None):
    headers = {
        "Content-Type": "application/json",
        "X-Agent-Slack-Server": server_id,
    }
    if request_id:
        headers["Idempotency-Key"] = request_id
    request = Request(
        f"{base_url}{path}",
        data=json.dumps(payload).encode(),
        headers=headers,
        method="POST",
    )
    with urlopen(request, timeout=5) as response:
        body = response.read().decode()
        if response.headers.get_content_type() == "application/x-ndjson":
            return [json.loads(line) for line in body.splitlines() if line]
        return json.loads(body)


def test_voice_capabilities_and_malformed_requests_are_real_http_contracts(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_SLACK_CLI", "offline")
    with _running_server(tmp_path) as (base_url, server_id):
        capabilities = _request(base_url, server_id, "/api/v1/voice/capabilities")
        assert capabilities["enabled"] is True
        assert capabilities["files"]["preview"] is True

        try:
            _post(base_url, server_id, "/api/v1/voice/utterances", {"chat_id": "", "text": ""})
        except HTTPError as error:
            assert error.code == 400
        else:
            raise AssertionError("malformed voice request was accepted")


def test_voice_retry_is_deduplicated_under_concurrent_http_requests(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_SLACK_CLI", "offline")
    with _running_server(tmp_path) as (base_url, server_id):
        chat = _post(base_url, server_id, "/api/v1/chats", {"title": "Voice", "member_ids": ["coordinator"], "kind": "direct"})
        app = __import__("agent_slack.server", fromlist=["_Handler"])._Handler.manager.active_app()
        calls = []

        def stream(**_kwargs):
            calls.append(True)
            yield {"type": "agent_started", "agent_id": "coordinator", "agent_label": "System Coordinator"}
            yield {"type": "delta", "agent_id": "coordinator", "text": "Done"}
            yield {"type": "agent_completed", "agent_id": "coordinator"}

        app.orchestrator.stream_agent_reply = stream
        payload = {"chat_id": chat["chat_id"], "text": "Create the report", "request_id": "same-voice-request"}
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: _post(base_url, server_id, "/api/v1/voice/utterances", payload), range(2)))

        refreshed = _request(base_url, server_id, f"/api/v1/chats/{chat['chat_id']}")
        user_messages = [message for message in refreshed["messages"] if message["author_type"] == "user"]
        assert len(user_messages) == 1
        assert sum(1 for result in results if isinstance(result, list)) == 1
        assert len(calls) == 1


def test_conversational_voice_turn_uses_isolated_chat_and_returns_backend_speech(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_SLACK_CLI", "offline")
    with _running_server(tmp_path) as (base_url, server_id):
        source = _post(base_url, server_id, "/api/v1/chats", {"title": "Text chat", "member_ids": ["coordinator"], "kind": "direct"})
        app = __import__("agent_slack.server", fromlist=["_Handler"])._Handler.manager.active_app()
        app.voice.transcribe = lambda _audio, _suffix: "Find the report"
        app.voice.synthesize = lambda _text, _voice=None: b"AIFF backend speech"

        def stream(**_kwargs):
            yield {"type": "agent_completed", "agent_id": "coordinator", "message": "The report is ready."}

        app.orchestrator.stream_agent_reply = stream
        voice = _post(base_url, server_id, "/api/v1/voice/sessions", {"source_chat_id": source["chat_id"]})
        assert voice["kind"] == "voice"
        request = Request(
            f"{base_url}/api/v1/voice/turns",
            data=b"fake audio",
            headers={
                "Content-Type": "audio/wav",
                "Content-Length": "10",
                "X-Agent-Slack-Server": server_id,
                "X-Agent-Slack-Voice-Chat": voice["chat_id"],
                "Idempotency-Key": "voice-turn-1",
            },
            method="POST",
        )
        with urlopen(request, timeout=5) as response:
            events = [json.loads(line) for line in response.read().decode().splitlines() if line]
        event_types = [event["type"] for event in events]
        assert event_types[0] == "voice_transcript"
        assert "agent_completed" in event_types
        assert event_types[-1] == "speech_ready"
        assert events[-1]["url"].startswith("/api/v1/voice/speech/")
        source_messages = _request(base_url, server_id, f"/api/v1/chats/{source['chat_id']}")["messages"]
        assert not [message for message in source_messages if message["author_type"] == "user"]
        persisted = _request(base_url, server_id, f"/api/v1/chats/{voice['chat_id']}")
        assert [message["body"] for message in persisted["messages"] if message["author_type"] == "user"] == ["Find the report"]


def test_created_artifact_preview_is_clickable_and_path_is_not_client_url(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_SLACK_CLI", "offline")
    with _running_server(tmp_path) as (base_url, server_id):
        chat = _post(base_url, server_id, "/api/v1/chats", {"title": "Files", "member_ids": ["coordinator"], "kind": "direct"})
        app = __import__("agent_slack.server", fromlist=["_Handler"])._Handler.manager.active_app()

        def stream(**_kwargs):
            (app.project_root / "created.md").write_text("# Created", encoding="utf-8")
            yield {"type": "agent_started", "agent_id": "coordinator", "agent_label": "System Coordinator"}
            yield {"type": "delta", "agent_id": "coordinator", "text": "Created it"}
            yield {"type": "agent_completed", "agent_id": "coordinator"}

        app.orchestrator.stream_agent_reply = stream
        _post(base_url, server_id, f"/api/v1/chats/{chat['chat_id']}/messages", {"body": "Make a file"})
        events = _post(base_url, server_id, "/api/v1/voice/utterances", {"chat_id": chat["chat_id"], "text": "Make it", "request_id": "file-run"})
        assert events[-1]["type"] == "run_completed"
        refreshed = _request(base_url, server_id, f"/api/v1/chats/{chat['chat_id']}")
        attachment = refreshed["messages"][-1]["metadata"]["attachments"][0]
        assert attachment["url"].startswith("/api/artifacts/")
        with urlopen(f"{base_url}{attachment['url']}", timeout=5) as response:
            assert response.read() == b"# Created"
