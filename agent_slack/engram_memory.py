"""Small, opt-in Streamable HTTP bridge to Engram's remote-web MCP toolset.

Workspace and bearer-token selection comes from administrator configuration, never
from a caller supplied Engram scope. Responses are returned to the current MCP
caller; this bridge does not broadcast memory to a Slack channel or create a copy.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import urlparse


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class EngramMemoryBridge:
    MAX_RESPONSE_BYTES = 1_000_000

    def __init__(self, endpoint: str | None, workspaces: dict[str, Any], timeout: float = 10.0):
        self.endpoint = endpoint.rstrip("/") if endpoint else None
        self.workspaces = workspaces
        self.timeout = timeout
        if self.endpoint:
            parsed = urlparse(self.endpoint)
            if parsed.scheme != "https" and parsed.hostname not in ("127.0.0.1", "localhost", "::1"):
                raise ValueError("Engram MCP endpoint must use HTTPS")
            if parsed.path not in ("", "/mcp") or parsed.query or parsed.fragment:
                raise ValueError("Engram endpoint must be the /mcp URL")
            self.endpoint = self.endpoint if parsed.path == "/mcp" else self.endpoint + "/mcp"

    @classmethod
    def from_environment(cls) -> "EngramMemoryBridge":
        raw = os.environ.get("AGENT_SLACK_ENGRAM_WORKSPACES_JSON", "{}")
        try:
            workspaces = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError("AGENT_SLACK_ENGRAM_WORKSPACES_JSON must be valid JSON") from exc
        if not isinstance(workspaces, dict):
            raise ValueError("Engram workspace configuration must be an object")
        return cls(os.environ.get("AGENT_SLACK_ENGRAM_MCP_URL"), workspaces)

    def _config(self, workspace_key: str) -> tuple[dict[str, Any], str]:
        if not self.endpoint:
            raise RuntimeError("Engram integration is not configured")
        config = self.workspaces.get(workspace_key)
        if not isinstance(config, dict):
            raise PermissionError("Engram workspace is not configured for this Agent Slack workspace")
        token_env = config.get("tokenEnv")
        token = os.environ.get(token_env, "") if isinstance(token_env, str) else ""
        if len(token) < 32:
            raise RuntimeError("Configured Engram credential is missing or too short")
        scope = config.get("scope")
        if not isinstance(scope, dict) or scope.get("type") != "workspace" or not scope.get("workspaceId"):
            raise ValueError("Engram workspace configuration requires a fixed workspace scope")
        return config, token

    def _post(self, token: str, payload: dict[str, Any], session_id: str | None = None) -> tuple[dict[str, Any], str | None]:
        assert self.endpoint
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json",
                   "Accept": "application/json, text/event-stream"}
        if session_id:
            headers["MCP-Session-Id"] = session_id
        request = urllib.request.Request(self.endpoint, json.dumps(payload).encode(), headers, method="POST")
        opener = urllib.request.build_opener(_NoRedirect)
        try:
            with opener.open(request, timeout=self.timeout) as response:
                session_id = response.headers.get("Mcp-Session-Id", session_id)
                content_type = response.headers.get("Content-Type", "")
                data = response.read(self.MAX_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as exc:
            if exc.code in (301, 302, 303, 307, 308):
                raise RuntimeError("Engram MCP redirects are refused to protect the bearer credential") from None
            raise RuntimeError(f"Engram MCP returned HTTP {exc.code}") from None
        except (urllib.error.URLError, TimeoutError) as exc:
            raise RuntimeError("Engram MCP is unavailable; retry with the same idempotency key for writes") from exc
        if len(data) > self.MAX_RESPONSE_BYTES:
            raise RuntimeError("Engram MCP response exceeded the 1 MB Agent Slack limit")
        if not data:
            return {}, session_id
        if "text/event-stream" in content_type:
            messages = [line[6:].strip() for line in data.decode().splitlines() if line.startswith("data:")]
            if not messages:
                raise RuntimeError("Engram MCP returned an empty event stream")
            result = json.loads(messages[-1])
        else:
            result = json.loads(data)
        if not isinstance(result, dict) or "error" in result:
            raise RuntimeError("Engram MCP returned an invalid or failed protocol response")
        return result, session_id

    def call(self, workspace_key: str, tool: str, arguments: dict[str, Any]) -> Any:
        _config, token = self._config(workspace_key)
        init, session_id = self._post(token, {"jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-03-26", "capabilities": {},
                        "clientInfo": {"name": "agent-slack", "version": "1"}}})
        if not isinstance(init.get("result"), dict) or not init["result"].get("protocolVersion"):
            raise RuntimeError("Engram MCP initialization failed")
        self._post(token, {"jsonrpc": "2.0", "method": "notifications/initialized"}, session_id)
        response, _ = self._post(token, {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                                         "params": {"name": tool, "arguments": arguments}}, session_id)
        result = response.get("result")
        if not isinstance(result, dict):
            raise RuntimeError("Engram MCP returned no tool result")
        if result.get("isError"):
            raise RuntimeError("Engram memory operation was denied or failed")
        structured = result.get("structuredContent")
        if isinstance(structured, dict) and "result" in structured:
            return structured["result"]
        content = result.get("content", [])
        if content and isinstance(content[0], dict) and isinstance(content[0].get("text"), str):
            return json.loads(content[0]["text"])
        return result

    def write(self, workspace_key: str, tool: str, arguments: dict[str, Any]) -> Any:
        config, _token = self._config(workspace_key)
        if config.get("allowWrites") is not True:
            raise PermissionError("Engram writes are disabled for this Agent Slack workspace")
        if tool == "engram_create":
            scope = dict(config["scope"])
            if config.get("collectionId"):
                scope["collectionId"] = config["collectionId"]
            arguments = {**arguments, "scope": scope}
        return self.call(workspace_key, tool, arguments)
