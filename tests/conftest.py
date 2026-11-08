from __future__ import annotations

import sys
import urllib.request
from pathlib import Path

import pytest


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))

DESKTOP_SECRET = "test-desktop-secret"


@pytest.fixture(autouse=True)
def _authorized_test_servers(tmp_path, monkeypatch):
    """The HTTP handler now requires authentication. Servers that the tests start by hand get an auth store and the
    private desktop credential, and local test requests carry it unless they already set their own header."""
    from agent_slack.auth import AuthStore
    from agent_slack.server import _Handler

    monkeypatch.setattr(_Handler, "auth", AuthStore(tmp_path / "auth-state"), raising=False)
    monkeypatch.setattr(_Handler, "desktop_secret", DESKTOP_SECRET, raising=False)
    original = urllib.request.Request.__init__

    def init(self, url, data=None, headers=None, *args, **kwargs):
        headers = dict(headers or {})
        if isinstance(url, str) and url.startswith("http://127.0.0.1"):
            headers.setdefault("X-Agent-Slack-Desktop", DESKTOP_SECRET)
        original(self, url, data, headers, *args, **kwargs)

    monkeypatch.setattr(urllib.request.Request, "__init__", init)
