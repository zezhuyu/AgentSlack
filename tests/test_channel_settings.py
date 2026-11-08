from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.request import Request, urlopen

from agent_slack.server import _Handler
from agent_slack.servers import AgentServerManager


@contextmanager
def _running(tmp_path: Path, project_root: Path):
    _Handler.manager = AgentServerManager(Path(__file__).resolve().parents[1], tmp_path / "state")
    _Handler.manager.add_server(project_root)
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _request(base: str, method: str, payload: dict | None = None):
    request = Request(
        f"{base}/api/channel-settings",
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={"Content-Type": "application/json"},
        method=method,
    )
    with urlopen(request, timeout=10) as response:
        return response.status, json.loads(response.read())


def test_channel_settings_update_only_slack_connector(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    manifest = {
        "schema_version": 2,
        "workspace_key": "test-workspace",
        "connectors": {"other": {"enabled": True}, "slack": {"enabled": False, "channel_id": "COLD"}},
    }
    (project_root / ".agent-slack.json").write_text(json.dumps(manifest), encoding="utf-8")

    with _running(tmp_path, project_root) as base:
        assert _request(base, "GET")[1] == {"enabled": False, "channel_id": "COLD"}
        status, saved = _request(base, "POST", {"enabled": True, "channel_id": "C0123456789"})

    assert status == 200
    assert saved == {"enabled": True, "channel_id": "C0123456789"}
    result = json.loads((project_root / ".agent-slack.json").read_text(encoding="utf-8"))
    assert result["workspace_key"] == "test-workspace"
    assert result["connectors"]["other"] == {"enabled": True}
    assert result["connectors"]["slack"] == saved


def test_channel_settings_reject_invalid_channel_id(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    with _running(tmp_path, project_root) as base:
        try:
            _request(base, "POST", {"enabled": True, "channel_id": "#general"})
        except Exception as error:
            assert getattr(error, "code", None) == 400
        else:
            raise AssertionError("invalid channel ID should be rejected")
