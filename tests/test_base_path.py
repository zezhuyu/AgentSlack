from __future__ import annotations

import http.client
import io
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_slack.auth import AuthStore
from agent_slack.server import _Handler, AgentSlackHTTPServer
from agent_slack.servers import AgentServerManager


class BasePathTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        static = self.root / "static"
        static.mkdir()
        (static / "index.html").write_text("app root", encoding="utf-8")
        (static / "asset.js").write_text("asset", encoding="utf-8")
        (static / "agent_slackfoo").write_text("unstripped", encoding="utf-8")
        manager = AgentServerManager(self.root, self.root / "data", static_root=static)
        _Handler.manager = manager
        _Handler.auth = AuthStore(manager.data_root)
        _Handler.auth.setup("correct-horse-battery", _Handler.auth.data["setup_token"])
        _Handler.desktop_secret = "base-path-test-secret"
        self.server = AgentSlackHTTPServer(("127.0.0.1", 0), _Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = ("127.0.0.1", self.server.server_port)
        self.env = patch.dict("os.environ", {"AGENT_SLACK_BASE_PATH": "/agent_slack/"})
        self.env.start()

    def tearDown(self) -> None:
        self.env.stop()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temp.cleanup()

    def request(self, method: str, path: str, *, headers: dict[str, str] | None = None, body: bytes | None = None):
        connection = http.client.HTTPConnection(*self.base, timeout=5)
        merged = {"X-Agent-Slack-Desktop": "base-path-test-secret", **(headers or {})}
        connection.request(method, path, body=body, headers=merged)
        response = connection.getresponse()
        result = response.status, dict(response.getheaders()), response.read()
        connection.close()
        return result

    def test_root_and_prefixed_api_and_static_routes_work(self) -> None:
        for path in ("/api/health", "/agent_slack/api/health"):
            status, _headers, body = self.request("GET", path)
            self.assertEqual(status, 200, body)
            self.assertTrue(b'"ok": true' in body)
        self.assertEqual(self.request("GET", "/")[2], b"app root")
        self.assertEqual(self.request("GET", "/agent_slack/asset.js")[2], b"asset")
        with patch.dict("os.environ", {"AGENT_SLACK_BASE_PATH": "/custom/"}):
            self.assertEqual(self.request("GET", "/custom/api/health")[0], 200)
        with patch.dict("os.environ", {"AGENT_SLACK_BASE_PATH": ""}):
            status, _headers, body = self.request("GET", "/agent_slack/api/health")
            self.assertEqual((status, body), (200, b"app root"))

    def test_mount_redirect_preserves_query_and_similar_prefix_is_not_stripped(self) -> None:
        status, headers, _body = self.request("GET", "/agent_slack?next=%2Fapi")
        self.assertEqual(status, 308)
        self.assertEqual(headers["Location"], "/agent_slack/?next=%2Fapi")
        status, _headers, body = self.request("GET", "/agent_slackfoo")
        self.assertEqual(status, 200)
        self.assertEqual(body, b"unstripped")

    def test_prefixed_and_root_websocket_upgrade_paths_reach_voice_dispatch(self) -> None:
        headers = {"Upgrade": "websocket", "Sec-WebSocket-Key": "dGhlIHNhbXBsZSBub25jZQ=="}
        for path in ("/api/v1/chat/voice", "/agent_slack/api/v1/chat/voice"):
            status, _headers, _body = self.request("GET", path, headers=headers)
            self.assertEqual(status, 409)  # Dispatch reached the WS handler; no agent server is configured.

    def test_ndjson_response_keeps_streaming_headers_and_flushes_events(self) -> None:
        handler = _Handler.__new__(_Handler)
        headers = {}
        handler.send_response = lambda status: headers.update(status=status)
        handler.send_header = lambda name, value: headers.update({name: value})
        handler.end_headers = lambda: None
        handler.wfile = io.BytesIO()
        handler._ndjson([{"type": "event"}])
        self.assertEqual(headers["Content-Type"], "application/x-ndjson; charset=utf-8")
        self.assertEqual(headers["Cache-Control"], "no-cache, no-transform")
        self.assertEqual(headers["X-Accel-Buffering"], "no")
        self.assertEqual(handler.wfile.getvalue(), b'{"type": "event"}\n')

    def test_login_cookie_path_and_secure_flag_follow_proxy_prefix(self) -> None:
        payload = b'{"password":"correct-horse-battery"}'
        status, headers, _body = self.request(
            "POST", "/agent_slack/api/auth/login",
            headers={"X-Forwarded-Proto": "https", "Content-Type": "application/json"}, body=payload,
        )
        self.assertEqual(status, 200)
        self.assertIn("Path=/agent_slack", headers["Set-Cookie"])
        self.assertIn("HttpOnly", headers["Set-Cookie"])
        self.assertIn("SameSite=Strict", headers["Set-Cookie"])
        self.assertIn("Secure", headers["Set-Cookie"])
        status, cleared, _body = self.request(
            "POST", "/agent_slack/api/auth/logout",
            headers={"X-Forwarded-Proto": "https", "Cookie": headers["Set-Cookie"].split(";", 1)[0]},
        )
        self.assertEqual(status, 200)
        self.assertIn("Path=/agent_slack", cleared["Set-Cookie"])
        self.assertIn("Max-Age=0", cleared["Set-Cookie"])
        self.assertIn("Secure", cleared["Set-Cookie"])

    def test_forwarded_prefix_is_validated_for_login_cookie(self) -> None:
        payload = b'{"password":"correct-horse-battery"}'
        for supplied, expected in (("/mount/v2/", "Path=/mount/v2"), ("/bad/../path", "Path=/"), ("/bad//path", "Path=/"), ("https://evil", "Path=/")):
            status, headers, _body = self.request(
                "POST", "/api/auth/login",
                headers={"X-Forwarded-Prefix": supplied, "Content-Type": "application/json"}, body=payload,
            )
            self.assertEqual(status, 200)
            self.assertIn(expected, headers["Set-Cookie"])


if __name__ == "__main__":
    unittest.main()
