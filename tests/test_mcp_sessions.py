from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

import pytest

from agent_slack.mcp_server import AgentSlackMCP, handle_jsonrpc, _tool_definitions
from agent_slack.servers import AgentServerManager


def _setup(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("AGENT_SLACK_CLI", "offline")
    project = tmp_path / "project"
    (project / ".claude" / "agents").mkdir(parents=True)
    (project / ".claude" / "agents" / "helper.md").write_text("# Helper\n", encoding="utf-8")
    (project / ".agent-slack.json").write_text(json.dumps({"schema_version": 2, "workspace_key": "one", "runner": "codex"}), encoding="utf-8")
    manager = AgentServerManager(tmp_path / "app", tmp_path / "data")
    manager.add_server(project)
    adapter = AgentSlackMCP(manager)
    app = manager.app_for_workspace("one")

    def stream(**_kwargs):
        yield {"type": "agent_started", "agent_id": "helper"}
        yield {"type": "delta", "agent_id": "helper", "text": "ok"}
        yield {"type": "agent_completed", "agent_id": "helper", "message": "ok"}

    app.orchestrator.stream_agent_reply = stream
    return adapter, app


def _call(adapter, args, meta=None, tool="create_task", ident=1):
    params = {"name": tool, "arguments": {"workspace_key": "one", "agent_id": "helper", "instruction": args}}
    if meta is not None:
        params["_meta"] = meta
    return handle_jsonrpc(adapter, {"jsonrpc": "2.0", "id": ident, "method": "tools/call", "params": params})


def _value(response):
    assert "error" not in response
    return json.loads(response["result"]["content"][0]["text"])


def _wait_for(predicate):
    deadline = time.time() + 10
    while time.time() < deadline:
        if predicate():
            return
        time.sleep(.01)
    assert predicate()


def test_meta_reuses_chat_per_conversation_and_agent(tmp_path, monkeypatch):
    adapter, app = _setup(tmp_path, monkeypatch)
    (app.project_root / ".claude" / "agents" / "reviewer.md").write_text("# Reviewer\n", encoding="utf-8")
    app.refresh_agents()
    def stream(**_kwargs):
        yield {"type": "agent_started", "agent_id": "helper"}
        yield {"type": "delta", "agent_id": "helper", "text": "ok"}
        yield {"type": "agent_completed", "agent_id": "helper", "message": "ok"}
    app.orchestrator.stream_agent_reply = stream
    first = _value(_call(adapter, "first", {"openai/session": "private-conversation-1"}, ident=1))
    second = _value(_call(adapter, "second", {"openai/session": "private-conversation-1"}, ident=2))
    other = _value(_call(adapter, "third", {"openai/session": "private-conversation-2"}, ident=3))
    _wait_for(lambda: all(app.get_task(task_id)["status"] in {"completed", "failed"}
                          for task_id in (first["task_id"], second["task_id"])))
    _wait_for(lambda: len([m for m in app.get_chat(first["chat_id"])["messages"] if m["author_type"] == "user"]) == 2)
    assert len([m for m in app.get_chat(first["chat_id"])["messages"] if m["author_type"] == "user"]) == 2, app.list_tasks()
    assert first["chat_id"] == second["chat_id"]
    assert second["session_reused"] is True
    assert first["task_id"] != second["task_id"]
    assert other["chat_id"] != first["chat_id"]
    different_agent = handle_jsonrpc(adapter, {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {
        "name": "create_task", "_meta": {"openai/session": "private-conversation-1"},
        "arguments": {"workspace_key": "one", "agent_id": "reviewer", "instruction": "other agent"}}})
    assert _value(different_agent)["chat_id"] != first["chat_id"]
    chat = app.get_chat(first["chat_id"])
    assert [m["body"] for m in chat["messages"] if m["author_type"] == "user"] == ["first", "second"]
    data = (app.data_root / "mcp_sessions.json").read_text()
    assert "private-conversation-1" not in data
    assert os.stat(app.data_root / "mcp_sessions.json").st_mode & 0o777 == 0o600


def test_explicit_token_no_identity_and_deleted_chat(tmp_path, monkeypatch):
    adapter, app = _setup(tmp_path, monkeypatch)
    app._run_project_task = lambda _task_id: None
    first = _value(_call(adapter, "first", None, ident=11))
    second = _value(_call(adapter, "second", None, ident=12))
    assert first["chat_id"] != second["chat_id"]
    resumed = _value(_call(adapter, "third", None, ident=13))
    assert resumed["chat_id"] != first["chat_id"]
    by_token = _value(_call(adapter, "fourth", None, ident=14))
    # Token is passed through the ordinary optional argument.
    assert by_token["session_id"]
    args = {"workspace_key": "one", "agent_id": "helper", "instruction": "resume", "session_id": first["session_id"]}
    response = handle_jsonrpc(adapter, {"jsonrpc": "2.0", "id": 15, "method": "tools/call", "params": {"name": "create_task", "arguments": args}})
    resumed = _value(response)
    assert resumed["chat_id"] == first["chat_id"] and resumed["session_reused"] is True
    app.storage.delete_chat(first["chat_id"])
    args["instruction"] = "rebind"
    rebound = _value(handle_jsonrpc(adapter, {"jsonrpc": "2.0", "id": 16, "method": "tools/call", "params": {"name": "create_task", "arguments": args}}))
    assert rebound["chat_id"] != first["chat_id"]


def test_invalid_tokens_and_other_tools_metadata_are_safe(tmp_path, monkeypatch):
    adapter, _app = _setup(tmp_path, monkeypatch)
    for value in ("bad space", "x" * 129, "as_invalid"):
        params = {"name": "create_task", "arguments": {"workspace_key": "one", "agent_id": "helper", "instruction": "x", "session_id": value}}
        result = handle_jsonrpc(adapter, {"jsonrpc": "2.0", "id": 20, "method": "tools/call", "params": params})
        assert result["error"]["code"] == -32000
    result = handle_jsonrpc(adapter, {"jsonrpc": "2.0", "id": 21, "method": "tools/call", "params": {"name": "get_project_status", "arguments": {"workspace_key": "one", "session_id": "ignored"}, "_meta": {"openai/session": "x"}}})
    assert "error" not in result
    schemas = {item["name"]: item["inputSchema"]["properties"] for item in _tool_definitions()}
    assert "session_id" in schemas["create_task"] and "session_id" in schemas["run_project_skill"]
    assert "session_id" not in schemas["get_project_status"]
    descriptions = {item["name"]: item["description"] for item in _tool_definitions()}
    assert all("Pass the session_id returned by earlier calls in this conversation" in descriptions[name]
               for name in ("create_task", "run_project_skill"))


def test_http_mcp_reuses_conversation_identity(tmp_path, monkeypatch):
    from http.server import ThreadingHTTPServer
    from threading import Thread
    from urllib.request import Request, urlopen
    from agent_slack.server import _Handler

    adapter, app = _setup(tmp_path, monkeypatch)
    _Handler.manager = adapter.manager
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        responses = []
        for ident, prompt in ((31, "http one"), (32, "http two")):
            body = json.dumps({"jsonrpc": "2.0", "id": ident, "method": "tools/call", "params": {
                "name": "create_task", "_meta": {"openai/session": "http-conversation"},
                "arguments": {"workspace_key": "one", "agent_id": "helper", "instruction": prompt}}}).encode()
            request = Request(f"http://127.0.0.1:{server.server_port}/mcp", data=body,
                              headers={"Content-Type": "application/json"}, method="POST")
            with urlopen(request, timeout=5) as response:
                responses.append(json.loads(response.read()))
        tasks = [_value(value) for value in responses]
        assert tasks[0]["chat_id"] == tasks[1]["chat_id"]
        assert tasks[1]["session_reused"] is True
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_same_session_tasks_execute_fifo_and_queue_status_stays_queued(tmp_path, monkeypatch):
    adapter, app = _setup(tmp_path, monkeypatch)
    entered = threading.Event()
    release = threading.Event()
    order = []

    def slow_stream(**kwargs):
        order.append(kwargs["objective"])
        if kwargs["objective"] == "first slow":
            entered.set()
            assert release.wait(3)
        yield {"type": "agent_started", "agent_id": "helper"}
        yield {"type": "delta", "agent_id": "helper", "text": "done"}
        yield {"type": "agent_completed", "agent_id": "helper", "message": "done"}

    app.orchestrator.stream_agent_reply = slow_stream
    first = _value(_call(adapter, "first slow", {"openai/session": "serial-session"}, ident=41))
    assert entered.wait(3), app.get_task(first["task_id"])
    second = _value(_call(adapter, "second queued", {"openai/session": "serial-session"}, ident=42))
    assert app.get_task(second["task_id"])["status"] == "queued"
    release.set()
    _wait_for(lambda: all(app.get_task(task_id)["status"] == "completed"
                          for task_id in (first["task_id"], second["task_id"])))
    assert order == ["first slow", "second queued"]
