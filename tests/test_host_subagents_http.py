import http.client
import json
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

from agent_slack.auth import AuthStore
from agent_slack import host_sessions
from agent_slack.server import _Handler
from agent_slack.servers import AgentServerManager


def _write(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))


def test_subagent_detail_endpoint_is_authenticated_read_only_and_parent_scoped(tmp_path, monkeypatch):
    claude, codex = tmp_path / "claude", tmp_path / "codex"
    _write(codex / "2026" / "11" / "19" / "rollout-parent.jsonl", [
        {"type": "session_meta", "payload": {"id": "parent", "cwd": str(tmp_path)}},
        {"type": "response_item", "payload": {"type": "message", "role": "user", "content": "parent task"}},
        {"type": "event_msg", "payload": {"type": "task_started"}},
    ])
    _write(codex / "2026" / "11" / "19" / "rollout-child.jsonl", [
        {"type": "session_meta", "payload": {"id": "child", "cwd": str(tmp_path), "thread_source": "subagent", "parent_thread_id": "parent", "agent_nickname": "Scout", "agent_role": "explore"}},
        {"type": "response_item", "payload": {"type": "message", "role": "user", "content": "look around"}},
        {"type": "response_item", "payload": {"type": "message", "role": "assistant", "phase": "final_answer", "content": "safe final"}},
        {"type": "event_msg", "payload": {"type": "task_complete"}},
    ])
    monkeypatch.setattr(host_sessions, "_roots", lambda: (claude, codex))
    host_sessions._SUMMARY_CACHE.clear()
    (tmp_path / "static").mkdir()
    (tmp_path / "static" / "index.html").write_text("ok")
    _Handler.manager = AgentServerManager(tmp_path, tmp_path / "data", static_root=tmp_path / "static")
    _Handler.auth = AuthStore(tmp_path / "auth")
    _Handler.desktop_secret = "host-session-test"
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def request(method, path, body=None, authorized=False):
        conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=4)
        headers = {"Content-Type": "application/json"}
        if authorized: headers["X-Agent-Slack-Desktop"] = "host-session-test"
        conn.request(method, path, json.dumps(body).encode() if body is not None else None, headers)
        response = conn.getresponse()
        result = response.status, json.loads(response.read() or b"{}")
        conn.close()
        return result

    try:
        endpoint = "/api/host-sessions/parent/subagents/child"
        assert request("GET", endpoint)[0] == 401
        status, detail = request("GET", endpoint, authorized=True)
        assert status == 200 and detail["result_preview"] == "safe final"
        assert request("GET", "/api/host-sessions/other/subagents/child", authorized=True)[0] == 404
        assert request("POST", "/api/host-sessions/child/resume", {"body": "resume"}, authorized=True)[0] == 400
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
