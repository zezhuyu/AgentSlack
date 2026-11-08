"""HTTP conformance of the /mcp JSON-RPC endpoint: malformed and oversized requests get explicit errors, valid ones still work."""
from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from http.client import HTTPConnection
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from agent_slack.server import AgentSlackHTTPServer, MAX_MCP_BODY_BYTES, _Handler
from agent_slack.servers import AgentServerManager


@contextmanager
def _running(tmp_path: Path):
    app_root = Path(__file__).resolve().parents[1]
    _Handler.manager = AgentServerManager(app_root, tmp_path / "state")
    server = AgentSlackHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _post(base: str, body: bytes):
    request = Request(f"{base}/mcp", data=body, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urlopen(request, timeout=10) as response:
            return response.status, response.read()
    except HTTPError as error:
        return error.code, error.read()


def test_malformed_json_is_a_parse_error_not_a_silent_accept(tmp_path: Path) -> None:
    with _running(tmp_path) as base:
        status, body = _post(base, b"{not json")
        assert status == 400
        assert json.loads(body)["error"]["code"] == -32700


def test_oversized_request_is_refused_without_reading_it(tmp_path: Path) -> None:
    with _running(tmp_path) as base:
        # Declare an oversized body but do not send it: the refusal is decided from the header, and the body is never read.
        connection = HTTPConnection(base.removeprefix("http://"), timeout=10)
        connection.putrequest("POST", "/mcp")
        connection.putheader("Content-Type", "application/json")
        connection.putheader("Content-Length", str(MAX_MCP_BODY_BYTES + 1))
        connection.endheaders()
        response = connection.getresponse()
        assert response.status == 413
        assert json.loads(response.read())["error"]["code"] == -32600
        connection.close()
        status, body = _post(base, json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}).encode())
        assert status == 200 and "tools" in json.loads(body)["result"], "the server is still serving after the refusal"


def test_notification_still_gets_an_empty_accepted_reply(tmp_path: Path) -> None:
    with _running(tmp_path) as base:
        status, body = _post(base, json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}).encode())
        assert status == 202 and body == b""
