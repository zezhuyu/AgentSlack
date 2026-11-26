from __future__ import annotations

import http.client
import json
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from agent_slack import host_sessions
from agent_slack.auth import AuthStore
from agent_slack.server import _Handler
from agent_slack.servers import AgentServerManager
from agent_slack.titles import TitleStore, clean_user_title, is_injected, short_title


@pytest.mark.parametrize("text,expected", [
    ("i want this app to support deploy behind the reverse proxy server, so the user can use https://x/agent_slack",
     "Support deploy behind the reverse proxy server"),
    ("Can you please fix the login redirect bug? It loops forever", "Fix the login redirect bug?"),
    ("why the finished task still not able to send the notification for activate session?",
     "Why the finished task still not able to send…"),
    ("<command-name>/review</command-name><command-args>--fix auth</command-args>", "/review --fix auth"),
    ("You are the RECON agent. Do NOT modify any files. Read .orchestrate/recon.md for the question.", "Recon"),
    ("You are the EXECUTOR and own this step end to end. Read .orchestrate/step-1.md fully, then do", "Executor: step 1"),
    ("```python\nprint(1)\n```\nrefactor the chat part", "Refactor the chat part"),
])
def test_short_title_names_prompts_compactly(text, expected):
    assert short_title(text) == expected


def test_injected_context_is_never_a_title_and_titles_are_bounded():
    for text in ("# AGENTS.md instructions for /x\nrules", "<environment_context>x</environment_context>",
                 "<task-notification><task-id>1</task-id></task-notification>", ""):
        assert short_title(text) == ""
    assert is_injected("  <system-reminder>hi")
    assert len(short_title("word " * 60)) <= 49
    assert clean_user_title("  a\x00b\n  c  ") == "ab c" and len(clean_user_title("x" * 200)) == 60


def _write(path: Path, rows: list[dict]) -> Path:
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    return path


def _claude(role: str, text: str, **extra) -> dict:
    return {"type": role, "cwd": "/tmp/proj", "message": {"role": role, "content": [{"type": "text", "text": text}]}, **extra}


def test_claude_titles_prefer_custom_then_ai_then_the_first_real_prompt(tmp_path, monkeypatch):
    plain = _write(tmp_path / "plain.jsonl", [_claude("user", "<system-reminder>ctx</system-reminder>"),
                                              _claude("user", "add a dark mode toggle to settings"), _claude("assistant", "ok")])
    ai = _write(tmp_path / "ai.jsonl", [_claude("user", "x y z"), {"type": "ai-title", "aiTitle": "Dark mode settings"}, _claude("assistant", "ok")])
    custom = _write(tmp_path / "custom.jsonl", [_claude("user", "x y z"), {"type": "ai-title", "aiTitle": "AI name"},
                                                {"type": "custom-title", "customTitle": "My name"}, _claude("assistant", "ok")])
    monkeypatch.setattr(host_sessions, "_session_files", lambda: [(plain, "claude"), (ai, "claude"), (custom, "claude")])
    monkeypatch.setattr(host_sessions, "_roots", lambda: (tmp_path / "none", tmp_path / "none"))
    host_sessions._SUMMARY_CACHE.clear()
    got = {s["session_id"]: (s["title"], s["title_source"]) for s in host_sessions.list_host_sessions()}
    assert got == {"plain": ("Add a dark mode toggle to settings", "generated"),
                   "ai": ("Dark mode settings", "ai"), "custom": ("My name", "custom")}


def test_codex_title_uses_thread_name_or_reaches_the_prompt_past_huge_injected_context(tmp_path, monkeypatch):
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    huge = "developer rules " * 40_000                      # ~640 KB before the first real user message
    named = _write(sessions / "rollout-named.jsonl", [{"type": "session_meta", "payload": {"id": "named", "cwd": str(tmp_path)}},
                                                      {"type": "response_item", "payload": {"type": "message", "role": "user", "content": "hello there"}}])
    deep = _write(sessions / "rollout-deep.jsonl", [
        {"type": "session_meta", "payload": {"id": "deep", "cwd": str(tmp_path), "base_instructions": "b" * 300_000}},
        {"type": "response_item", "payload": {"type": "message", "role": "developer", "content": huge}},
        {"type": "response_item", "payload": {"type": "message", "role": "user", "content": "# AGENTS.md instructions for /x\n" + huge}},
        {"type": "response_item", "payload": {"type": "message", "role": "user", "content": "<environment_context>cwd</environment_context>"}},
        {"type": "response_item", "payload": {"type": "message", "role": "user", "content": "Investigate slow dashboard loading"}},
    ])
    (tmp_path / "session_index.jsonl").write_text(json.dumps({"id": "named", "thread_name": "Diagnose website slowness"}) + "\n")
    monkeypatch.setattr(host_sessions, "_session_files", lambda: [(named, "codex"), (deep, "codex")])
    monkeypatch.setattr(host_sessions, "_roots", lambda: (tmp_path / "none", sessions))
    host_sessions._SUMMARY_CACHE.clear(); host_sessions._FIRST_PROMPTS.clear()
    got = {s["session_id"]: (s["title"], s["title_source"]) for s in host_sessions.list_host_sessions()}
    assert got["named"] == ("Diagnose website slowness", "thread")
    assert got["deep"] == ("Investigate slow dashboard loading", "generated")


def _server(tmp_path: Path):
    static = tmp_path / "static"
    static.mkdir()
    (static / "index.html").write_text("ok")
    manager = AgentServerManager(tmp_path, tmp_path / "data", static_root=static)
    _Handler.manager, _Handler.auth, _Handler.desktop_secret = manager, AuthStore(tmp_path / "auth"), "ui-secret"
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    def request(method, path, body=None, auth=True):
        conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        headers = {"Content-Type": "application/json", **({"X-Agent-Slack-Desktop": "ui-secret"} if auth else {})}
        conn.request(method, path, json.dumps(body).encode() if body is not None else None, headers)
        response = conn.getresponse()
        result = response.status, json.loads(response.read() or b"{}")
        conn.close()
        return result
    return server, manager, request


def test_rename_endpoints_require_login_and_host_names_override_then_clear(tmp_path, monkeypatch):
    transcript = _write(tmp_path / "abc.jsonl", [_claude("user", "add a dark mode toggle"), _claude("assistant", "ok")])
    monkeypatch.setattr(host_sessions, "_session_files", lambda: [(transcript, "claude")])
    monkeypatch.setattr(host_sessions, "_roots", lambda: (tmp_path / "none", tmp_path / "none"))
    host_sessions._SUMMARY_CACHE.clear()
    server, manager, request = _server(tmp_path)
    try:
        # Every mutating route is behind the login, including the pre-existing server settings route.
        assert request("PATCH", "/api/host-sessions/abc/title", {"title": "x"}, auth=False)[0] == 401
        assert request("PATCH", "/api/chats/c1", {"title": "x"}, auth=False)[0] == 401
        assert request("PATCH", "/api/servers/nope", {"name": "x"}, auth=False)[0] == 401
        assert request("GET", "/api/runners", auth=False)[0] == 401
        assert request("POST", "/api/chats", {"title": "x"}, auth=False)[0] == 401
        assert request("PATCH", "/api/host-sessions/missing/title", {"title": "x"})[0] == 404

        status, renamed = request("PATCH", "/api/host-sessions/abc/title", {"title": "  Dark mode  work "})
        assert (status, renamed["title"], renamed["title_source"]) == (200, "Dark mode work", "custom")
        listed = request("GET", "/api/host-sessions")[1]["sessions"][0]
        assert (listed["title"], listed["title_source"]) == ("Dark mode work", "custom")
        assert request("GET", "/api/host-sessions/abc")[1]["title"] == "Dark mode work"
        assert oct((Path(manager.data_root) / "session_titles.json").stat().st_mode & 0o777) == "0o600"

        cleared = request("PATCH", "/api/host-sessions/abc/title", {"title": ""})[1]
        assert (cleared["title"], cleared["title_source"]) == ("Add a dark mode toggle", "generated")
    finally:
        server.shutdown()


def test_task_chats_are_named_from_the_request_and_legacy_task_names_are_derived(tmp_path, monkeypatch):
    from agent_slack.app import AgentSlackApp
    monkeypatch.setenv("AGENT_SLACK_CLI", "offline")
    project, app_root = tmp_path / "project", tmp_path / "app"
    agent = project / ".claude" / "agents" / "helper.md"
    agent.parent.mkdir(parents=True)
    app_root.mkdir()
    agent.write_text("---\nname: helper\nsummary: test\n---\n# Helper\n")
    app = AgentSlackApp(project, app_root)
    legacy = app.create_chat("Task 0123456789ab", ["helper"], kind="task")
    app.storage.append_message(legacy["chat_id"], {"author_type": "user", "author_id": "x", "body": "Review the NO-GO recommendation for EODHD", "metadata": {}})
    titles = {c["chat_id"]: c["title"] for c in app.list_chats()}
    assert titles[legacy["chat_id"]] == "Review the NO-GO recommendation for EODHD"
    assert app.rename_chat(legacy["chat_id"], "Mine")["title"] == "Mine"
    assert {c["chat_id"]: c["title"] for c in app.list_chats()}[legacy["chat_id"]] == "Mine"
    with pytest.raises(KeyError):
        app.rename_chat("missing", "x")


def test_title_store_persists_and_clears(tmp_path):
    store = TitleStore(tmp_path)
    assert store.set("s1", " Hello ") == "Hello"
    assert TitleStore(tmp_path).get_all() == {"s1": "Hello"}
    store.set("s1", "")
    assert store.get_all() == {}
