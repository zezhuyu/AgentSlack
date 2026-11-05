from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import os
import time
from pathlib import Path
from threading import RLock
from typing import Any


class AuthStore:
    """Password and per-install device credentials for the web/mobile API."""

    def __init__(self, data_root: Path):
        self.path = data_root / "auth.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = RLock()
        try:
            self.data: dict[str, Any] = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            self.data = {}
        except (OSError, ValueError) as exc:
            raise RuntimeError("Authentication state could not be read; refusing to disable authentication") from exc
        if not self.data.get("setup_token") and not self.data.get("password_hash"):
            self.data["setup_token"] = secrets.token_urlsafe(32)
        self.sessions: dict[str, float] = {}
        self._save()

    def _save(self) -> None:
        temp = self.path.with_suffix(".tmp")
        temp.write_text(json.dumps(self.data, separators=(",", ":")), encoding="utf-8")
        try:
            os.chmod(temp, 0o600)
        except OSError:
            pass
        temp.replace(self.path)

    @staticmethod
    def _password_hash(password: str, salt: bytes | None = None) -> str:
        salt = salt or secrets.token_bytes(16)
        digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 310_000)
        return f"{salt.hex()}:{digest.hex()}"

    def status(self) -> dict[str, bool]:
        return {"password_set": bool(self.data.get("password_hash"))}

    def setup(self, password: str, setup_token: str | None) -> bool:
        with self.lock:
            if self.data.get("password_hash"):
                return False
            expected = str(self.data.get("setup_token") or "")
            if not expected or not setup_token or not hmac.compare_digest(expected, setup_token):
                return False
            if len(password) < 10:
                raise ValueError("Password must be at least 10 characters")
            self.data["password_hash"] = self._password_hash(password)
            self.data.pop("setup_token", None)
            self._save()
            return True

    def _verify_password(self, password: str) -> bool:
        stored = str(self.data.get("password_hash") or "")
        try:
            salt_hex, digest_hex = stored.split(":", 1)
            actual = self._password_hash(password, bytes.fromhex(salt_hex)).split(":", 1)[1]
            return hmac.compare_digest(actual, digest_hex)
        except (ValueError, TypeError):
            return False

    def login(self, password: str, device_name: str | None = None) -> tuple[str, bool]:
        with self.lock:
            if not self.data.get("password_hash") or not self._verify_password(password):
                raise PermissionError("Incorrect password")
            if device_name is None:
                token = secrets.token_urlsafe(32)
                self.sessions[token] = time.time() + 60 * 60 * 24 * 14
                return token, False
            token = secrets.token_urlsafe(32)
            devices = self.data.setdefault("devices", {})
            device_id = secrets.token_hex(16)
            devices[hashlib.sha256(token.encode()).hexdigest()] = {
                "device_id": device_id,
                "name": device_name[:80] or "iOS device",
                "created_at": int(time.time()),
            }
            self._save()
            return token, True

    def authorized(self, bearer: str | None, cookie: str | None, desktop_secret: str | None, expected_desktop_secret: str | None) -> bool:
        if expected_desktop_secret and desktop_secret and hmac.compare_digest(desktop_secret, expected_desktop_secret):
            return True
        if not self.data.get("password_hash"):
            return False
        if bearer:
            key = hashlib.sha256(bearer.encode()).hexdigest()
            return key in self.data.get("devices", {})
        if cookie:
            expiry = self.sessions.get(cookie, 0)
            if expiry > time.time():
                return True
            self.sessions.pop(cookie, None)
        return False

    def change_password(self, current: str, password: str) -> None:
        with self.lock:
            if not self._verify_password(current):
                raise PermissionError("Current password is incorrect")
            if len(password) < 10:
                raise ValueError("Password must be at least 10 characters")
            self.data["password_hash"] = self._password_hash(password)
            self._save()

    def revoke_devices(self) -> int:
        with self.lock:
            count = len(self.data.get("devices", {}))
            self.data["devices"] = {}
            self._save()
            return count

    def device_tokens(self) -> list[dict[str, Any]]:
        return list(self.data.get("devices", {}).values())
