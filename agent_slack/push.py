"""Optional Web Push support using RFC 8291 encryption and RFC 8292 VAPID."""
from __future__ import annotations

import base64
import json
import logging
import os
import re
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

logger = logging.getLogger(__name__)
try:
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    CRYPTO_AVAILABLE = True
except ImportError:  # optional dependency
    CRYPTO_AVAILABLE = False

_LOCK = threading.RLock()


def _ssl_context() -> ssl.SSLContext:
    """python.org/PyInstaller builds ship no CA bundle, so look for one explicitly."""
    for loader in (lambda: __import__("certifi").where(), lambda: "/etc/ssl/cert.pem"):
        try:
            cafile = loader()
        except Exception:
            continue
        if cafile and os.path.exists(cafile):
            return ssl.create_default_context(cafile=cafile)
    return ssl.create_default_context()

MAX_SUBSCRIPTIONS = 20


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _unb64(value: str, expected: int | None = None) -> bytes:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        raise ValueError("invalid base64url value")
    try:
        decoded = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except Exception as exc:
        raise ValueError("invalid base64url value") from exc
    if expected is not None and len(decoded) != expected:
        raise ValueError("invalid key size")
    return decoded


def _atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    finally:
        temporary.unlink(missing_ok=True)


def _state(data_root: Path) -> dict:
    if not CRYPTO_AVAILABLE:
        return {"subscriptions": []}
    path = Path(data_root) / "push.json"
    with _LOCK:
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(state, dict) and state.get("private_key") and state.get("subscriptions") is not None:
                os.chmod(path, 0o600)
                return state
        except (OSError, json.JSONDecodeError):
            pass
        private = ec.generate_private_key(ec.SECP256R1())
        private_pem = private.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode("ascii")
        public = private.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
        state = {"private_key": private_pem, "public_key": _b64(public), "subscriptions": []}
        _atomic_json(path, state)
        return state


def config(data_root: Path) -> dict:
    if not CRYPTO_AVAILABLE:
        return {"enabled": False, "public_key": None, "reason": "Install requirements-push.txt to enable push notifications"}
    try:
        state = _state(data_root)
        return {"enabled": True, "public_key": state["public_key"], "reason": None}
    except Exception as exc:
        logger.warning("Could not initialize push state: %s", exc)
        return {"enabled": False, "public_key": None, "reason": "Push notifications could not be initialized"}


def _valid_endpoint(endpoint: str) -> bool:
    parsed = urllib.parse.urlsplit(endpoint)
    if parsed.scheme == "https" and parsed.hostname:
        return True
    return bool(os.getenv("AGENT_SLACK_PUSH_TEST_HTTP") == "1" and parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost"})


def _valid_subscription(subscription: dict) -> dict:
    if not isinstance(subscription, dict):
        raise ValueError("subscription must be an object")
    endpoint = str(subscription.get("endpoint") or "")
    keys = subscription.get("keys") or {}
    if not isinstance(keys, dict):
        raise ValueError("subscription keys must be an object")
    if len(endpoint) > 4096 or not _valid_endpoint(endpoint):
        raise ValueError("endpoint must be a valid HTTPS URL")
    _unb64(keys.get("p256dh", ""), 65)
    _unb64(keys.get("auth", ""), 16)
    return {"endpoint": endpoint, "keys": {"p256dh": keys["p256dh"], "auth": keys["auth"]}}


def subscribe(data_root: Path, subscription: dict, base_url: str, origin: str, label: str = "", user_agent: str = "") -> dict:
    if not CRYPTO_AVAILABLE:
        raise RuntimeError("Push notifications require cryptography")
    clean = _valid_subscription(subscription)
    parsed_base, parsed_origin = urllib.parse.urlsplit(base_url), urllib.parse.urlsplit(origin)
    if parsed_base.scheme not in {"http", "https"} or not parsed_base.netloc or parsed_base.username or parsed_base.password:
        raise ValueError("base_url must be an absolute HTTP(S) URL")
    if (parsed_base.scheme, parsed_base.netloc) != (parsed_origin.scheme, parsed_origin.netloc):
        raise ValueError("base_url must be same-origin")
    if parsed_base.query or parsed_base.fragment:
        raise ValueError("base_url must not include a query or fragment")
    with _LOCK:
        state = _state(data_root)
        old = next((s for s in state["subscriptions"] if s["endpoint"] == clean["endpoint"]), None)
        if old:
            old.update(clean, base_url=base_url, label=str(label)[:80], user_agent=str(user_agent)[:200])
            result = old
        else:
            if len(state["subscriptions"]) >= MAX_SUBSCRIPTIONS:
                raise ValueError("subscription limit reached")
            result = {"id": uuid.uuid4().hex, **clean, "base_url": base_url, "created": int(time.time()), "label": str(label)[:80], "user_agent": str(user_agent)[:200]}
            state["subscriptions"].append(result)
        _atomic_json(Path(data_root) / "push.json", state)
        return {"ok": True, "id": result["id"]}


def unsubscribe(data_root: Path, endpoint: str) -> dict:
    with _LOCK:
        state = _state(data_root)
        before = len(state["subscriptions"])
        state["subscriptions"] = [s for s in state["subscriptions"] if s.get("endpoint") != endpoint]
        if len(state["subscriptions"]) != before:
            _atomic_json(Path(data_root) / "push.json", state)
        return {"ok": True, "removed": before - len(state["subscriptions"])}


def _jwt(private, endpoint: str, default_subject: str = "mailto:agent-slack@localhost") -> str:
    parsed = urllib.parse.urlsplit(endpoint)
    audience = f"{parsed.scheme}://{parsed.netloc}"
    subject = os.getenv("AGENT_SLACK_PUSH_SUBJECT") or default_subject
    subject_url = urllib.parse.urlsplit(subject)
    if not ((subject_url.scheme == "mailto" and "@" in subject_url.path) or (subject_url.scheme == "https" and subject_url.netloc)):
        raise ValueError("AGENT_SLACK_PUSH_SUBJECT must be mailto: or https:")
    header, claims = _b64(b'{"typ":"JWT","alg":"ES256"}'), _b64(json.dumps({"aud": audience, "exp": int(time.time()) + 3600, "sub": subject}, separators=(",", ":")).encode())
    signing = f"{header}.{claims}".encode()
    der = private.sign(signing, ec.ECDSA(hashes.SHA256()))
    from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
    r, s = decode_dss_signature(der)
    return f"{header}.{claims}.{_b64(r.to_bytes(32, 'big') + s.to_bytes(32, 'big'))}"


def encrypt(payload: bytes, p256dh: str, auth: str, *, salt: bytes | None = None, private_key=None) -> bytes:
    ua_public = _unb64(p256dh, 65)
    auth_secret = _unb64(auth, 16)
    private_key = private_key or ec.generate_private_key(ec.SECP256R1())
    sender_public = private_key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    peer = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_public)
    shared = private_key.exchange(ec.ECDH(), peer)
    ikm = HKDF(algorithm=hashes.SHA256(), length=32, salt=auth_secret, info=b"WebPush: info\0" + ua_public + sender_public).derive(shared)
    salt = salt or os.urandom(16)
    cek = HKDF(algorithm=hashes.SHA256(), length=16, salt=salt, info=b"Content-Encoding: aes128gcm\0").derive(ikm)
    nonce = HKDF(algorithm=hashes.SHA256(), length=12, salt=salt, info=b"Content-Encoding: nonce\0").derive(ikm)
    ciphertext = AESGCM(cek).encrypt(nonce, payload + b"\x02", None)
    return salt + (4096).to_bytes(4, "big") + bytes([len(sender_public)]) + sender_public + ciphertext


def _send_one(state: dict, subscription: dict, payload: dict, timeout: float = 3.0) -> None:
    private = serialization.load_pem_private_key(state["private_key"].encode(), password=None)
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
    endpoint = subscription["endpoint"]
    # Apple rejects placeholder subjects like mailto:...@localhost (BadJwtToken); default to the https origin
    # the subscriber registered from, which is a real, resolvable contact URL.
    base = urllib.parse.urlsplit(str(subscription.get("base_url") or ""))
    default_subject = f"https://{base.netloc}" if base.scheme == "https" and base.netloc else "mailto:agent-slack@localhost"
    jwt = _jwt(private, endpoint, default_subject)
    encrypted = encrypt(body, subscription["keys"]["p256dh"], subscription["keys"]["auth"])
    req = urllib.request.Request(endpoint, data=encrypted, headers={
        "Content-Encoding": "aes128gcm", "Content-Type": "application/notification+json", "TTL": "86400", "Urgency": "normal",
        "Authorization": f"vapid t={jwt}, k={state['public_key']}",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_ssl_context()) as response:
            response.read(1)
    except urllib.error.HTTPError as exc:
        if exc.code in {404, 410}:
            raise
        logger.warning("Push service returned HTTP %s", exc.code)
    except Exception as exc:
        logger.warning("Push delivery failed: %s", exc)


def send(data_root: Path, payload: dict) -> None:
    if not CRYPTO_AVAILABLE:
        return
    try:
        state = _state(data_root)
    except Exception:
        return
    for subscription in list(state.get("subscriptions", [])):
        try:
            outgoing = json.loads(json.dumps(payload))
            notification = outgoing.get("notification") or {}
            base_url = str(subscription.get("base_url") or "")
            navigate = notification.get("navigate")
            if navigate is None:
                chat_id = str(notification.get("chat_id") or "")
                navigate = base_url.rstrip("/") + (f"/#chat={urllib.parse.quote(chat_id)}" if chat_id else "/")
            elif not re.match(r"https?://", str(navigate)):
                # Declarative Web Push requires an absolute URL; deep links like "#host=<id>" are relative to the app.
                navigate = urllib.parse.urljoin(base_url, str(navigate))
            notification["navigate"] = navigate
            notification.pop("chat_id", None)
            _send_one(state, subscription, outgoing)
        except urllib.error.HTTPError as exc:
            if exc.code in {404, 410}:
                unsubscribe(data_root, subscription["endpoint"])
            else:
                logger.warning("Push delivery failed: HTTP %s", exc.code)
        except Exception as exc:
            logger.warning("Push delivery failed: %s", exc)


def notify_run_finished(data_root: Path, *, title: str, body: str, base_url: str = "", chat_id: str = "", deep_link: str = "", failed: bool = False, cancelled: bool = False, app_badge: int | None = None) -> None:
    if cancelled or not CRYPTO_AVAILABLE:
        return
    try:
        existing = json.loads((Path(data_root) / "push.json").read_text(encoding="utf-8"))
        if not existing.get("subscriptions"):
            return
    except (OSError, json.JSONDecodeError, AttributeError):
        return
    body = re.sub(r"[`*_>#]+", "", str(body)).replace("\r", " ").replace("\n", " ")
    body = re.sub(r"\s+", " ", body).strip()[:140] or ("Run failed" if failed else "Agent run completed")
    navigate = deep_link or (base_url.rstrip("/") + (f"/#chat={urllib.parse.quote(chat_id)}" if chat_id else "/") if base_url else None)
    notification = {"title": str(title or "Agent Slack")[:80], "body": body, "navigate": navigate, "chat_id": chat_id, "silent": False, "tag": f"agent-slack-{chat_id or uuid.uuid4().hex}"}
    payload = {"web_push": 8030, "notification": notification}
    if app_badge is not None:
        payload["app_badge"] = max(0, int(app_badge))
    threading.Thread(target=send, args=(Path(data_root), payload), daemon=True).start()


def send_test(data_root: Path) -> None:
    if not CRYPTO_AVAILABLE:
        raise RuntimeError("Push notifications require cryptography")
    state = _state(data_root)
    if not state.get("subscriptions"):
        raise ValueError("No push subscriptions are registered")
    payload = {"web_push": 8030, "notification": {"title": "Agent Slack", "body": "Push notifications are working.", "navigate": None, "silent": False, "tag": "agent-slack-test"}}
    threading.Thread(target=send, args=(Path(data_root), payload), daemon=True).start()
