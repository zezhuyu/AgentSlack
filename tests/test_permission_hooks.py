import http.server
import json
import os
import subprocess
import threading
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class HookHandler(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length))
        self.server.payloads.append(payload)
        if self.headers.get("X-Agent-Slack-Hook-Token") != "test-token":
            self.send_response(401)
            self.end_headers()
            return
        result = {"decision": payload.get("test_decision", "none"), "reason": "reviewed"}
        body = json.dumps(result).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_):
        pass


def run_hook(script, payload, env):
    return subprocess.run(
        ["node", str(ROOT / "scripts/hooks" / script)], input=json.dumps(payload), text=True,
        capture_output=True, env=env, timeout=15,
    )


def test_permission_hook_allow_deny_none_and_token_failures(tmp_path):
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), HookHandler)
    server.payloads = []
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = os.environ.copy()
        base.update({
            "AGENT_SLACK_URL": f"http://127.0.0.1:{server.server_port}",
            "AGENT_SLACK_DATA_ROOT": str(tmp_path),
            "AGENT_SLACK_HOOK_TOKEN": "test-token",
            "AGENT_SLACK_HOOK_WAIT": "0",
        })
        for cli, source in (("claude-permission.mjs", "claude"), ("codex-permission.mjs", "codex")):
            for decision in ("allow", "deny"):
                result = run_hook(cli, {"tool_name": "Bash", "test_decision": decision}, base)
                assert result.returncode == 0 and result.stderr == ""
                output = json.loads(result.stdout)
                assert output["hookSpecificOutput"]["hookEventName"] == "PermissionRequest"
                assert output["hookSpecificOutput"]["decision"]["behavior"] == decision
                assert server.payloads[-1]["source"] == source
            result = run_hook(cli, {"tool_name": "Bash", "test_decision": "none"}, base)
            assert result.returncode == 0 and result.stdout == ""

        no_token = dict(base)
        no_token.pop("AGENT_SLACK_HOOK_TOKEN")
        result = run_hook("claude-permission.mjs", {"tool_name": "Bash"}, no_token)
        assert result.returncode == 0 and result.stdout == ""
        wrong_token = dict(base, AGENT_SLACK_HOOK_TOKEN="wrong")
        result = run_hook("codex-permission.mjs", {"tool_name": "Bash", "test_decision": "allow"}, wrong_token)
        assert result.returncode == 0 and result.stdout == ""
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_permission_hook_reads_token_file_and_silences_network_errors(tmp_path):
    token_file = tmp_path / "hook-token"
    token_file.write_text("test-token\n")
    env = os.environ.copy()
    env.update({"AGENT_SLACK_DATA_ROOT": str(tmp_path), "AGENT_SLACK_URL": "http://127.0.0.1:1", "AGENT_SLACK_HOOK_WAIT": "0"})
    env.pop("AGENT_SLACK_HOOK_TOKEN", None)
    result = run_hook("claude-permission.mjs", {"tool_name": "Bash", "test_decision": "allow"}, env)
    assert result.returncode == 0 and result.stdout == ""


def test_installer_preview_apply_backup_and_idempotence(tmp_path):
    claude = tmp_path / "claude/settings.json"
    codex = tmp_path / "codex/hooks.json"
    claude.parent.mkdir()
    codex.parent.mkdir()
    claude.write_text(json.dumps({"keep": True, "hooks": {"Stop": [{"hooks": []}]}}))
    codex.write_text(json.dumps({"hooks": {"SessionStart": []}}))
    env = os.environ.copy()
    env.update({"AGENT_SLACK_CLAUDE_SETTINGS": str(claude), "AGENT_SLACK_CODEX_HOOKS": str(codex)})
    installer = str(ROOT / "scripts/hooks/install-hooks.py")

    preview = subprocess.run(["python3", installer], capture_output=True, text=True, env=env, check=True)
    assert "Preview only" in preview.stdout
    assert not list(tmp_path.rglob("*.bak-*"))

    subprocess.run(["python3", installer, "--apply"], capture_output=True, text=True, env=env, check=True)
    first = (json.loads(claude.read_text()), json.loads(codex.read_text()))
    assert first[0]["keep"] is True and "Stop" in first[0]["hooks"]
    assert len(first[0]["hooks"]["PermissionRequest"]) == 1
    assert len(first[1]["hooks"]["PermissionRequest"]) == 1
    time.sleep(0.002)
    subprocess.run(["python3", installer, "--apply"], capture_output=True, text=True, env=env, check=True)
    assert json.loads(claude.read_text()) == first[0]
    assert json.loads(codex.read_text()) == first[1]
    assert len(list(tmp_path.rglob("*.bak-*"))) == 4
