from __future__ import annotations

import threading
import time
import http.client
import json
from http.server import ThreadingHTTPServer
from pathlib import Path

from agent_slack.auth import AuthStore
from agent_slack.server import _Handler
from agent_slack.servers import AgentServerManager

from agent_slack.permissions import PermissionStore


def test_permission_redaction_truncation_and_wait_resolution():
    store = PermissionStore()
    record = store.create({"session_id": "s", "tool_name": "Bash", "tool_input": {"command": "x" * 5000, "api_key": "secret"}}, "claude")
    assert len(record["tool_input"]["command"]) == 4096
    assert record["tool_input"]["api_key"] == "[REDACTED]"
    result = {}
    waiter = threading.Thread(target=lambda: result.update(store.wait(record["request_id"], 2)))
    waiter.start()
    assert store.decide(record["request_id"], "allow", "ok")[0] == 200
    waiter.join(1)
    assert result == {"decision": "allow", "reason": "ok"}
    assert store.decide(record["request_id"], "deny")[0] == 409


def test_permission_timeout_and_pending_cap():
    store = PermissionStore()
    assert store.wait(store.create({}, "codex")["request_id"], 0) == {"decision": "none"}
    for _ in range(51):
        store.create({}, "codex")
    assert sum(record["status"] == "pending" for record in store.list()) <= 20
    assert sum(record["status"] == "pending" for record in store.records.values()) == 50


def test_permission_concurrent_decisions_are_idempotent():
    store = PermissionStore()
    rid = store.create({}, "claude")["request_id"]
    barrier = threading.Barrier(3)
    results = []
    def decide(value):
        barrier.wait()
        results.append(store.decide(rid, value)[0])
    threads = [threading.Thread(target=decide, args=(value,)) for value in ("allow", "deny")]
    for thread in threads: thread.start()
    barrier.wait()
    for thread in threads: thread.join()
    assert sorted(results) == [200, 409]


def test_http_hook_token_isolated_and_decision_unblocks(tmp_path, monkeypatch):
    static = tmp_path / "static"
    static.mkdir()
    (static / "index.html").write_text("ok")
    manager = AgentServerManager(tmp_path, tmp_path / "data", static_root=static)
    _Handler.manager = manager
    _Handler.auth = AuthStore(tmp_path / "auth")
    _Handler.desktop_secret = "ui-secret"
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    token_path = manager.data_root / "hook-token"
    try:
        token_path.write_text("test-hook-token")
        token_path.chmod(0o600)
        def request(method, path, body=None, headers=None):
            conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=4)
            conn.request(method, path, json.dumps(body).encode() if body is not None else None,
                         {"Content-Type": "application/json", **(headers or {})})
            response = conn.getresponse()
            result = response.status, json.loads(response.read() or b"{}")
            conn.close()
            return result
        assert request("POST", "/api/hooks/permission", {"source": "claude"})[0] == 401
        assert request("GET", "/api/permissions")[0] == 401
        assert request("GET", "/api/permissions", headers={"X-Agent-Slack-Desktop": "ui-secret"})[0] == 200
        push_calls = []
        monkeypatch.setattr("agent_slack.push.notify_run_finished", lambda *a, **kw: push_calls.append(kw))
        output = []
        def ingress():
            output.append(request("POST", "/api/hooks/permission?wait=3", {"source": "codex", "tool_name": "Bash"}, {"X-Agent-Slack-Hook-Token": "test-hook-token"}))
        pending = threading.Thread(target=ingress)
        pending.start()
        for _ in range(100):
            records = request("GET", "/api/permissions", headers={"X-Agent-Slack-Desktop": "ui-secret"})[1]["permissions"]
            if records: break
            time.sleep(.01)
        rid = records[0]["request_id"]
        # Neither anonymous callers nor the hook token may approve; only a logged-in UI session can.
        assert request("POST", f"/api/permissions/{rid}/decision", {"decision": "allow"})[0] == 401
        assert request("POST", f"/api/permissions/{rid}/decision", {"decision": "allow"}, {"X-Agent-Slack-Hook-Token": "test-hook-token"})[0] == 401
        assert request("POST", f"/api/permissions/{rid}/decision", {"decision": "allow"}, {"X-Agent-Slack-Desktop": "ui-secret"})[0] == 200
        pending.join(2)
        assert output == [(200, {"decision": "allow", "reason": ""})]
        assert push_calls[0]["title"] == "Permission needed"
        assert push_calls[0]["deep_link"] == f"#permission={rid}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_question_requires_answer_and_returns_answers():
    store = PermissionStore()
    record = store.create({"tool_name": "AskUserQuestion", "tool_input": {"questions": [{"question": "Pick?", "options": [{"label": "A"}]}]}}, "claude")
    assert store.decide(record["request_id"], "allow")[0] == 400
    assert store.decide(record["request_id"], "allow", answers={"Pick?": "A"})[0] == 200
    assert store.wait(record["request_id"], 0) == {"decision": "allow", "reason": "", "answers": {"Pick?": "A"}}
