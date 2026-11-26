from __future__ import annotations

import http.client
import json
import threading
from pathlib import Path

import pytest

from agent_slack.auth import AuthStore
from agent_slack.server import AgentSlackHTTPServer, _Handler
from agent_slack.servers import AgentServerManager


@pytest.fixture
def upload_server(tmp_path: Path):
    project = tmp_path / "project"
    project.mkdir()
    app_root = tmp_path / "app"
    app_root.mkdir()
    (project / "AGENTS.md").write_text("# Test\n", encoding="utf-8")
    manager = AgentServerManager(app_root, tmp_path / "data", initial_project_root=project)
    _Handler.manager = manager
    _Handler.auth = AuthStore(manager.data_root)
    _Handler.auth.setup("test-password", _Handler.auth.data["setup_token"])
    _Handler.desktop_secret = "upload-test-secret"
    server = AgentSlackHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_port, manager
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def request(port: int, path: str, body: bytes, *, authorized: bool = True, content_type: str = "image/png"):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    headers = {"Content-Type": content_type, "X-Agent-Slack-Filename": "test.png"}
    if authorized:
        headers["X-Agent-Slack-Desktop"] = "upload-test-secret"
    connection.request("POST", path, body=body, headers=headers)
    response = connection.getresponse()
    result = response.status, response.read()
    connection.close()
    return result


def test_upload_endpoint_stores_file_and_requires_auth(upload_server):
    port, manager = upload_server
    assert request(port, "/api/v1/uploads", b"secret", authorized=False)[0] == 401
    status, body = request(port, "/api/v1/uploads", b"image-bytes")
    assert status == 201
    saved = json.loads(body)
    assert Path(saved["path"]).read_bytes() == b"image-bytes"
    assert "/uploads/" in saved["path"]
    assert Path(saved["path"]).is_relative_to(manager.data_root / "servers")


def test_upload_endpoint_rejects_oversized_upload(upload_server):
    port, _manager = upload_server
    # Content length is rejected before the request body is read.
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    connection.request("POST", "/api/v1/uploads", body=b"x", headers={
        "X-Agent-Slack-Desktop": "upload-test-secret",
        "X-Agent-Slack-Filename": "large.bin",
        "Content-Type": "application/octet-stream",
        "Content-Length": str(50 * 1024 * 1024 + 1),
    })
    response = connection.getresponse()
    assert response.status == 413
    response.read()
    connection.close()
