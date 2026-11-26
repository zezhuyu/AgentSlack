from __future__ import annotations

import base64
import http.client
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from agent_slack import push
from agent_slack.auth import AuthStore
from agent_slack.app import AgentSlackApp
from agent_slack.server import AgentSlackHTTPServer, _Handler
from agent_slack.servers import AgentServerManager


def b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


class FakePushHandler(BaseHTTPRequestHandler):
    body = b""
    headers_seen = {}
    status = 201

    def do_POST(self):  # noqa: N802
        type(self).body = self.rfile.read(int(self.headers["Content-Length"]))
        type(self).headers_seen = dict(self.headers.items())
        self.send_response(type(self).status)
        self.end_headers()

    def log_message(self, *_args):
        pass


@pytest.fixture
def http_env(tmp_path, monkeypatch):
    static = tmp_path / "static"
    static.mkdir()
    (static / "index.html").write_text("push test")
    (static / "sw.js").write_text("self.addEventListener('push', () => {});")
    manager = AgentServerManager(tmp_path, tmp_path / "data", static_root=static)
    _Handler.manager = manager
    _Handler.auth = AuthStore(manager.data_root)
    _Handler.auth.setup("test-password", _Handler.auth.data["setup_token"])
    _Handler.desktop_secret = "push-test-secret"
    server = AgentSlackHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("AGENT_SLACK_PUSH_TEST_HTTP", "1")
    yield manager.data_root, server.server_port
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


def request(port, method, path, body=None, *, auth=True, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    req_headers = {"Content-Type": "application/json", **(headers or {})}
    if auth:
        req_headers["X-Agent-Slack-Desktop"] = "push-test-secret"
    conn.request(method, path, json.dumps(body).encode() if body is not None else None, req_headers)
    response = conn.getresponse()
    raw = response.read()
    try:
        decoded = json.loads(raw or b"{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        decoded = raw
    result = response.status, decoded
    conn.close()
    return result


def create_client_keys():
    private = ec.generate_private_key(ec.SECP256R1())
    public = private.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    return private, {"p256dh": b64(public), "auth": b64(os.urandom(16))}


def decrypt_reference(body: bytes, ua_private, auth_secret: bytes) -> bytes:
    """Independent RFC 8291 receiver derivation, intentionally separate from push.encrypt."""
    salt, record_size, key_id_len = body[:16], int.from_bytes(body[16:20], "big"), body[20]
    assert record_size == 4096
    sender_public = body[21:21 + key_id_len]
    ciphertext = body[21 + key_id_len:]
    ua_public = ua_private.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    sender = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), sender_public)
    shared_secret = ua_private.exchange(ec.ECDH(), sender)
    ikm = HKDF(algorithm=hashes.SHA256(), length=32, salt=auth_secret, info=b"WebPush: info\0" + ua_public + sender_public).derive(shared_secret)
    cek = HKDF(algorithm=hashes.SHA256(), length=16, salt=salt, info=b"Content-Encoding: aes128gcm\0").derive(ikm)
    nonce = HKDF(algorithm=hashes.SHA256(), length=12, salt=salt, info=b"Content-Encoding: nonce\0").derive(ikm)
    plaintext = AESGCM(cek).decrypt(nonce, ciphertext, None)
    assert plaintext.endswith(b"\x02")
    return plaintext[:-1]


def test_vapid_key_persistence_and_permissions(tmp_path):
    first = push.config(tmp_path)
    second = push.config(tmp_path)
    key_file = tmp_path / "push.json"
    assert first["enabled"] and first["public_key"] == second["public_key"]
    assert key_file.stat().st_mode & 0o777 == 0o600
    assert push._state(tmp_path)["private_key"]


def test_authenticated_push_endpoints_and_validation(http_env, monkeypatch):
    data_root, port = http_env
    assert request(port, "GET", "/api/push/config", auth=False)[0] == 401
    assert request(port, "GET", "/api/push/config")[0] == 200
    assert request(port, "GET", "/agent_slack/api/v1/push/config")[0] == 200
    static_conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    static_conn.request("GET", "/agent_slack/sw.js", headers={"X-Agent-Slack-Desktop": "push-test-secret"})
    static_response = static_conn.getresponse()
    static_status, static_headers = static_response.status, dict(static_response.getheaders())
    static_response.read()
    static_conn.close()
    assert static_status == 200
    assert static_headers["Cache-Control"] == "no-cache"
    assert static_headers["Content-Type"].startswith("application/javascript")
    private, keys = create_client_keys()
    endpoint = "https://push.example.test/send/abc"
    payload = {"subscription": {"endpoint": endpoint, "keys": keys}, "base_url": f"http://127.0.0.1:{port}/agent_slack/", "label": "test"}
    headers = {"Origin": f"http://127.0.0.1:{port}", "Host": f"127.0.0.1:{port}"}
    assert request(port, "POST", "/api/v1/push/subscribe", payload, auth=False, headers=headers)[0] == 401
    status, result = request(port, "POST", "/api/v1/push/subscribe", payload, headers=headers)
    assert status == 200 and result["ok"]
    assert len(push._state(data_root)["subscriptions"]) == 1
    assert request(port, "POST", "/api/push/subscribe", {**payload, "subscription": {"endpoint": "http://evil.test", "keys": keys}}, headers=headers)[0] == 400
    assert request(port, "POST", "/api/push/subscribe", {**payload, "base_url": "https://evil.test/"}, headers=headers)[0] == 400
    assert request(port, "POST", "/api/push/subscribe", {**payload, "subscription": {"endpoint": endpoint, "keys": {}}}, headers=headers)[0] == 400
    # Behind Caddy + Tailscale Serve the proxy rewrites Host/X-Forwarded-Proto; the browser Origin must win.
    proxied = {"Origin": "https://studio.example.ts.net", "Host": f"127.0.0.1:{port}", "X-Forwarded-Proto": "http"}
    gateway = {**payload, "base_url": "https://studio.example.ts.net/agent_slack/"}
    assert request(port, "POST", "/api/push/subscribe", gateway, headers=proxied)[0] == 200
    assert request(port, "POST", "/api/push/subscribe", {**gateway, "base_url": "https://evil.test/agent_slack/"}, headers=proxied)[0] == 400
    monkeypatch.setattr(push, "send_test", lambda _data_root: None)
    assert request(port, "POST", "/api/push/test", {})[0] == 200
    assert request(port, "POST", "/api/push/unsubscribe", {"endpoint": endpoint}) == (200, {"ok": True, "removed": 1})
    assert push._state(data_root)["subscriptions"] == []


def test_run_notification_decrypts_and_vapid_signature_verifies(http_env):
    data_root, api_port = http_env
    FakePushHandler.body = b""
    FakePushHandler.status = 201
    fake = ThreadingHTTPServer(("127.0.0.1", 0), FakePushHandler)
    threading.Thread(target=fake.serve_forever, daemon=True).start()
    ua_private, keys = create_client_keys()
    endpoint = f"http://127.0.0.1:{fake.server_port}/push"
    origin = f"http://127.0.0.1:{api_port}"
    status, _ = request(api_port, "POST", "/api/push/subscribe", {
        "subscription": {"endpoint": endpoint, "keys": keys}, "base_url": origin + "/agent_slack/",
    }, headers={"Origin": origin, "Host": f"127.0.0.1:{api_port}"})
    assert status == 200
    push.notify_run_finished(data_root, title="Codex", body="**Finished**\nwith answer", chat_id="chat-1")
    deadline = time.time() + 4
    while not FakePushHandler.body and time.time() < deadline:
        time.sleep(0.02)
    headers = {key.lower(): value for key, value in FakePushHandler.headers_seen.items()}
    assert headers["content-encoding"] == "aes128gcm"
    assert headers["content-type"] == "application/notification+json"
    assert headers["ttl"] == "86400" and headers["urgency"] == "normal"
    authorization = headers["authorization"]
    assert authorization.startswith("vapid t=")
    token = authorization.split("t=", 1)[1].split(",", 1)[0]
    jwt_head, jwt_claims, signature = token.split(".")
    claims = json.loads(unb64(jwt_claims))
    assert claims["aud"] == f"http://127.0.0.1:{fake.server_port}"
    assert 0 < claims["exp"] - int(time.time()) <= 12 * 60 * 60
    state = push._state(data_root)
    vapid_public = serialization.load_pem_private_key(state["private_key"].encode(), None).public_key()
    raw_sig = unb64(signature)
    der_sig = encode_dss_signature(int.from_bytes(raw_sig[:32], "big"), int.from_bytes(raw_sig[32:], "big"))
    vapid_public.verify(der_sig, f"{jwt_head}.{jwt_claims}".encode(), ec.ECDSA(hashes.SHA256()))
    payload = json.loads(decrypt_reference(FakePushHandler.body, ua_private, unb64(keys["auth"])))
    assert payload["web_push"] == 8030
    assert payload["notification"] == {
        "title": "Codex", "body": "Finished with answer", "navigate": origin + "/agent_slack/#chat=chat-1",
        "silent": False, "tag": "agent-slack-chat-1",
    }
    fake.shutdown()
    fake.server_close()


def test_run_notification_accepts_deep_link_without_changing_chat_link(tmp_path, monkeypatch):
    data_root = tmp_path / "data"
    data_root.mkdir()
    (data_root / "push.json").write_text(json.dumps({"subscriptions": [{"endpoint": "unused"}]}))
    sent = []
    ready = threading.Event()
    def capture(_root, payload):
        sent.append(payload)
        ready.set()
    monkeypatch.setattr(push, "send", capture)
    push.notify_run_finished(data_root, title="Host", body="Done", deep_link="#host=session")
    assert ready.wait(1)
    assert sent[-1]["notification"]["navigate"] == "#host=session"
    ready.clear()
    push.notify_run_finished(data_root, title="Chat", body="Done", base_url="https://example.test/agent_slack", chat_id="chat-1")
    assert ready.wait(1)
    assert sent[-1]["notification"]["navigate"] == "https://example.test/agent_slack/#chat=chat-1"


def test_410_removes_subscription_and_cancelled_or_missing_crypto_is_noop(http_env, monkeypatch):
    data_root, api_port = http_env
    FakePushHandler.body = b""
    FakePushHandler.status = 410
    fake = ThreadingHTTPServer(("127.0.0.1", 0), FakePushHandler)
    threading.Thread(target=fake.serve_forever, daemon=True).start()
    _, keys = create_client_keys()
    origin = f"http://127.0.0.1:{api_port}"
    endpoint = f"http://127.0.0.1:{fake.server_port}/push"
    push.subscribe(data_root, {"endpoint": endpoint, "keys": keys}, origin + "/", origin)
    push.notify_run_finished(data_root, title="X", body="cancelled", cancelled=True)
    assert not FakePushHandler.body
    push.send(data_root, {"notification": {"title": "X", "body": "Y", "navigate": None}})
    assert push._state(data_root)["subscriptions"] == []
    monkeypatch.setattr(push, "CRYPTO_AVAILABLE", False)
    assert push.config(data_root)["enabled"] is False
    push.notify_run_finished(data_root, title="X", body="Y")
    fake.shutdown()
    fake.server_close()


def test_subscription_limit_deduplicates_by_endpoint(tmp_path):
    _, keys = create_client_keys()
    origin = "https://agent.example"
    for i in range(push.MAX_SUBSCRIPTIONS):
        push.subscribe(tmp_path, {"endpoint": f"https://push.example/{i}", "keys": keys}, origin + "/app/", origin)
    push.subscribe(tmp_path, {"endpoint": "https://push.example/0", "keys": keys}, origin + "/new/", origin)
    with pytest.raises(ValueError, match="limit"):
        push.subscribe(tmp_path, {"endpoint": "https://push.example/overflow", "keys": keys}, origin + "/", origin)
    assert len(push._state(tmp_path)["subscriptions"]) == push.MAX_SUBSCRIPTIONS


def test_app_sends_one_notification_on_finish_and_skips_cancelled_run(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_SLACK_CLI", "offline")
    project = tmp_path / "project"
    app_root = tmp_path / "app"
    project.mkdir()
    app_root.mkdir()
    agent_path = project / ".claude" / "agents" / "coordinator.md"
    agent_path.parent.mkdir(parents=True)
    agent_path.write_text("---\nname: coordinator\nsummary: test\n---\n# Coordinator\n")
    app = AgentSlackApp(project, app_root)
    chat = app.create_chat("Coordinator", ["coordinator"], kind="direct")
    notifications = []
    monkeypatch.setattr(push, "notify_run_finished", lambda *args, **kwargs: notifications.append((args, kwargs)))

    def completed(**_kwargs):
        yield {"type": "agent_started", "agent_id": "coordinator", "agent_label": "Coordinator"}
        yield {"type": "delta", "agent_id": "coordinator", "text": "Answer done."}
        yield {"type": "agent_completed", "agent_id": "coordinator"}

    app.orchestrator.stream_agent_reply = completed
    events = list(app.stream_run(chat["chat_id"], mode="respond"))
    assert events[-1]["type"] == "run_completed"
    assert len(notifications) == 1
    assert notifications[0][1]["body"] == "Answer done."

    notifications.clear()

    def cancelled(**_kwargs):
        yield {"type": "agent_cancelled", "agent_id": "coordinator"}

    app.orchestrator.stream_agent_reply = cancelled
    events = list(app.stream_run(chat["chat_id"], mode="respond"))
    assert events[-1]["type"] == "run_cancelled"
    assert notifications == []



def test_per_server_app_notifies_against_the_install_wide_push_store(tmp_path, monkeypatch):
    """Subscriptions live in <data>/push.json; chat servers run from <data>/servers/<id>/."""
    monkeypatch.setenv("AGENT_SLACK_CLI", "offline")
    project, app_root = tmp_path / "project", tmp_path / "app"
    agent_path = project / ".claude" / "agents" / "coordinator.md"
    agent_path.parent.mkdir(parents=True)
    app_root.mkdir()
    agent_path.write_text("---\nname: coordinator\nsummary: test\n---\n# Coordinator\n")
    install_root = tmp_path / "data"
    app = AgentSlackApp(project, app_root, data_root=install_root / "servers" / "abc", push_root=install_root)
    chat = app.create_chat("Coordinator", ["coordinator"], kind="direct")
    calls = []
    monkeypatch.setattr(push, "notify_run_finished", lambda root, **kwargs: calls.append(root))

    def completed(**_kwargs):
        yield {"type": "agent_started", "agent_id": "coordinator", "agent_label": "Coordinator"}
        yield {"type": "agent_completed", "agent_id": "coordinator", "message": "Done."}

    app.orchestrator.stream_agent_reply = completed
    list(app.stream_run(chat["chat_id"], mode="respond"))
    assert calls == [install_root]


def test_relative_deep_links_become_absolute_navigate_urls(tmp_path, monkeypatch):
    """iOS drops declarative pushes whose navigate is not an absolute URL (e.g. a bare #host=<id>)."""
    private, keys = create_client_keys()
    push.subscribe(tmp_path, {"endpoint": "https://push.example.test/send/rel", "keys": keys},
                   "https://studio.example.ts.net/agent_slack/", "https://studio.example.ts.net")
    sent = []
    monkeypatch.setattr(push, "_send_one", lambda state, sub, payload, timeout=3.0: sent.append(payload["notification"]))
    for payload in (
        {"web_push": 8030, "notification": {"title": "T", "body": "B", "navigate": "#host=abc", "chat_id": ""}},
        {"web_push": 8030, "notification": {"title": "T", "body": "B", "navigate": "#permission=r1"}},
        {"web_push": 8030, "notification": {"title": "T", "body": "B", "navigate": None, "chat_id": "c1"}},
        {"web_push": 8030, "notification": {"title": "T", "body": "B", "navigate": "https://studio.example.ts.net/agent_slack/#x"}},
    ):
        push.send(tmp_path, payload)
    assert [n["navigate"] for n in sent] == [
        "https://studio.example.ts.net/agent_slack/#host=abc",
        "https://studio.example.ts.net/agent_slack/#permission=r1",
        "https://studio.example.ts.net/agent_slack/#chat=c1",
        "https://studio.example.ts.net/agent_slack/#x",
    ]
    assert all("chat_id" not in n for n in sent)
