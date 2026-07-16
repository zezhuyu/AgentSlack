from __future__ import annotations

import json
import mimetypes
import re
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from .servers import AgentServerManager


API_VERSION = "1"


def _no_server_payload() -> dict:
    return {
        "error": "No agent server is configured",
        "code": "server_not_configured",
        "setup": {
            "method": "POST",
            "endpoint": "/api/v1/servers",
            "required_fields": ["project_root"],
        },
    }


def _api_document() -> dict:
    return {
        "service": "agent-slack",
        "api_version": API_VERSION,
        "status": "ready",
        "base_path": "/api/v1",
        "openapi_url": "/api/v1/openapi.json",
        "server_selection": {
            "header": "X-Agent-Slack-Server",
            "description": "Optional server ID; otherwise the active server is used.",
        },
        "streaming": {
            "content_type": "application/x-ndjson",
            "endpoint": "/api/v1/chats/{chat_id}/run-stream",
        },
    }


def _openapi_document() -> dict:
    paths = {
        "/health": {"get": {"summary": "Service health and active agent system"}},
        "/servers": {
            "get": {"summary": "List registered agent systems"},
            "post": {"summary": "Register an agent-system folder"},
        },
        "/servers/{server_id}/activate": {"post": {"summary": "Activate an agent system"}},
        "/agents": {"get": {"summary": "List agents in the selected system"}},
        "/agents/discover": {"post": {"summary": "Refresh agent discovery"}},
        "/chats": {
            "get": {"summary": "List chats"},
            "post": {"summary": "Create a direct or group chat"},
        },
        "/chats/{chat_id}": {
            "get": {"summary": "Get a chat and its messages"},
            "delete": {"summary": "Delete a chat and its stored messages"},
        },
        "/chats/{chat_id}/messages": {"post": {"summary": "Post a user message"}},
        "/chats/{chat_id}/run-stream": {
            "post": {
                "summary": "Run agents and stream NDJSON events",
                "responses": {"200": {"description": "NDJSON event stream"}},
            }
        },
        "/runs/{run_id}/cancel": {
            "post": {"summary": "Stop an active agent-system run"},
        },
        "/runs/{run_id}/tasks/{task_id}/cancel": {
            "post": {"summary": "Stop one native task without stopping sibling tasks"},
        },
        "/chats/{chat_id}/meeting": {"post": {"summary": "Run a manual agent meeting"}},
        "/chats/{chat_id}/auto-meeting": {"post": {"summary": "Run an architecture-routed meeting"}},
    }
    return {
        "openapi": "3.1.0",
        "info": {"title": "Agent Slack API", "version": API_VERSION},
        "servers": [{"url": "/api/v1"}],
        "components": {
            "parameters": {
                "AgentServer": {
                    "name": "X-Agent-Slack-Server",
                    "in": "header",
                    "required": False,
                    "schema": {"type": "string"},
                }
            }
        },
        "paths": paths,
    }


def _versioned_path(path: str) -> str:
    if path == "/api/v1":
        return "/api"
    if path.startswith("/api/v1/"):
        return "/api/" + path.removeprefix("/api/v1/")
    return path


class _Handler(BaseHTTPRequestHandler):
    manager: AgentServerManager

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = _versioned_path(parsed.path)
        if path == "/api":
            return self._json(_api_document())
        if path == "/api/openapi.json":
            return self._json(_openapi_document())
        if path == "/api/health":
            requested_server_id = self.headers.get("X-Agent-Slack-Server", "").strip()
            try:
                active = self.manager.summary_for(requested_server_id) if requested_server_id else self.manager.active_summary()
            except KeyError as exc:
                return self._json({"error": str(exc).strip("'")}, status=404)
            architecture = self._active_app().architecture_summary() if active and active["available"] else None
            return self._json(
                {
                    "ok": True,
                    "workspace": active["name"] if active else None,
                    "active_server_id": self.manager.active_server_id,
                    "servers": self.manager.list_servers(),
                    "architecture": architecture,
                }
            )
        if path == "/api/servers":
            return self._json(
                {
                    "servers": self.manager.list_servers(),
                    "active_server_id": self.manager.active_server_id,
                }
            )
        match = re.fullmatch(r"/api/servers/([^/]+)/logo", path)
        if match:
            try:
                logo_path = self.manager.logo_path(match.group(1))
            except KeyError:
                return self._json({"error": "server not found"}, status=404)
            if logo_path is None:
                return self._json({"error": "server logo not found"}, status=404)
            return self._serve_file(logo_path)
        if not path.startswith("/api/"):
            return self._serve_static(parsed.path)
        if path.startswith("/api/") and self.manager.active_server_id is None:
            if path == "/api/agents":
                return self._json({"agents": []})
            if path == "/api/chats":
                return self._json({"chats": []})
            return self._json(_no_server_payload(), status=409)
        try:
            app = self._active_app()
        except KeyError as exc:
            return self._json({"error": str(exc).strip("'")}, status=404)
        except (LookupError, ValueError) as exc:
            return self._json({"error": str(exc)}, status=409)
        if path == "/api/agents":
            return self._json({"agents": app.list_agents()})
        if path == "/api/chats":
            return self._json({"chats": app.list_chats()})
        match = re.fullmatch(r"/api/chats/([^/]+)", path)
        if match:
            chat = app.get_chat(match.group(1))
            if chat is None:
                return self._json({"error": "chat not found"}, status=404)
            return self._json(chat)
        return self._serve_static(parsed.path)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = _versioned_path(parsed.path)
        payload = self._read_json_body()
        if path == "/api/servers":
            project_root = str(payload.get("project_root") or "").strip()
            if not project_root:
                return self._json({"error": "project_root is required"}, status=400)
            try:
                server = self.manager.add_server(
                    Path(project_root),
                    name=str(payload.get("name") or "") or None,
                    logo_path=(Path(str(payload["logo_path"])) if payload.get("logo_path") else None),
                )
            except ValueError as exc:
                return self._json({"error": str(exc)}, status=400)
            return self._json(server, status=201)
        match = re.fullmatch(r"/api/servers/([^/]+)/activate", path)
        if match:
            try:
                server = self.manager.activate(match.group(1))
            except KeyError as exc:
                return self._json({"error": str(exc)}, status=404)
            except ValueError as exc:
                return self._json({"error": str(exc)}, status=400)
            return self._json(server)
        if path.startswith("/api/") and self.manager.active_server_id is None:
            return self._json(_no_server_payload(), status=409)
        try:
            app = self._active_app()
        except KeyError as exc:
            return self._json({"error": str(exc).strip("'")}, status=404)
        except (LookupError, ValueError) as exc:
            return self._json({"error": str(exc)}, status=409)
        if path == "/api/agents/discover":
            app.reload_host_configuration()
            return self._json({"agents": app.refresh_agents()})
        if path == "/api/chats":
            chat = app.create_chat(
                title=str(payload.get("title") or "Untitled chat"),
                member_ids=list(payload.get("member_ids") or []),
                kind=str(payload.get("kind") or "group"),
            )
            return self._json(chat, status=201)
        match = re.fullmatch(r"/api/chats/([^/]+)/messages", path)
        if match:
            chat = app.add_user_message(match.group(1), str(payload.get("body") or ""))
            return self._json(chat, status=201)
        match = re.fullmatch(r"/api/chats/([^/]+)/run-stream", path)
        if match:
            try:
                events = app.stream_run(
                    chat_id=match.group(1),
                    mode=str(payload.get("mode") or "respond"),
                    objective=payload.get("objective"),
                    agent_ids=list(payload.get("agent_ids") or []),
                    lead_agent_id=payload.get("lead_agent_id"),
                    participant_ids=list(payload.get("participant_ids") or []),
                )
            except (KeyError, ValueError) as exc:
                return self._json({"error": str(exc)}, status=400)
            return self._ndjson(events)
        match = re.fullmatch(r"/api/runs/([^/]+)/cancel", path)
        if match:
            try:
                result = app.cancel_run(match.group(1))
            except KeyError as exc:
                return self._json({"error": str(exc).strip("'")}, status=404)
            return self._json(result, status=202)
        match = re.fullmatch(r"/api/runs/([^/]+)/tasks/([^/]+)/cancel", path)
        if match:
            try:
                result = app.cancel_task(match.group(1), match.group(2))
            except KeyError as exc:
                return self._json({"error": str(exc).strip("'")}, status=404)
            return self._json(result, status=202)
        match = re.fullmatch(r"/api/chats/([^/]+)/respond", path)
        if match:
            chat = app.run_agents(
                match.group(1),
                agent_ids=list(payload.get("agent_ids") or []),
                objective=payload.get("objective"),
            )
            return self._json(chat)
        match = re.fullmatch(r"/api/chats/([^/]+)/meeting", path)
        if match:
            lead_agent_id = str(payload.get("lead_agent_id") or "")
            objective = str(payload.get("objective") or "")
            if not lead_agent_id or not objective:
                return self._json({"error": "lead_agent_id and objective are required"}, status=400)
            chat = app.create_meeting(
                chat_id=match.group(1),
                lead_agent_id=lead_agent_id,
                participant_ids=list(payload.get("participant_ids") or []),
                objective=objective,
                auto_run=bool(payload.get("auto_run", True)),
            )
            return self._json(chat)
        match = re.fullmatch(r"/api/chats/([^/]+)/auto-meeting", path)
        if match:
            lead_agent_id = str(payload.get("lead_agent_id") or "")
            objective = str(payload.get("objective") or "")
            if not lead_agent_id or not objective:
                return self._json({"error": "lead_agent_id and objective are required"}, status=400)
            chat = app.auto_meeting(match.group(1), lead_agent_id=lead_agent_id, objective=objective)
            return self._json(chat)
        return self._json({"error": "not found"}, status=404)

    def do_PATCH(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = _versioned_path(parsed.path)
        payload = self._read_json_body()
        match = re.fullmatch(r"/api/servers/([^/]+)", path)
        if not match:
            return self._json({"error": "not found"}, status=404)
        try:
            server = self.manager.update_server(
                match.group(1),
                name=(str(payload["name"]) if "name" in payload else None),
                logo_path=(Path(str(payload["logo_path"])) if payload.get("logo_path") else None),
            )
        except KeyError as exc:
            return self._json({"error": str(exc)}, status=404)
        except ValueError as exc:
            return self._json({"error": str(exc)}, status=400)
        return self._json(server)

    def do_DELETE(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = _versioned_path(parsed.path)
        if path.startswith("/api/") and self.manager.active_server_id is None:
            return self._json(_no_server_payload(), status=409)
        match = re.fullmatch(r"/api/chats/([^/]+)", path)
        if not match:
            return self._json({"error": "not found"}, status=404)
        try:
            result = self._active_app().delete_chat(match.group(1))
        except KeyError as exc:
            return self._json({"error": str(exc).strip("'")}, status=404)
        except (LookupError, ValueError) as exc:
            return self._json({"error": str(exc)}, status=409)
        return self._json(result)

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(HTTPStatus.NO_CONTENT)
        self.send_header("Allow", "GET, POST, PATCH, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PATCH, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Agent-Slack-Server")
        self.send_header("X-Agent-Slack-Api-Version", API_VERSION)
        self.end_headers()

    def log_message(self, format: str, *args) -> None:  # noqa: A003
        return

    def _active_app(self):
        requested_server_id = self.headers.get("X-Agent-Slack-Server", "").strip()
        if requested_server_id:
            return self.manager.app_for(requested_server_id)
        return self.manager.active_app()

    def _read_json_body(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            return {}
        body = self.rfile.read(length)
        try:
            return json.loads(body.decode("utf-8"))
        except json.JSONDecodeError:
            return {}

    def _json(self, payload: dict, status: int = 200) -> None:
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("X-Agent-Slack-Api-Version", API_VERSION)
        self.end_headers()
        self.wfile.write(raw)

    def _ndjson(self, events) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
        self.send_header("Cache-Control", "no-cache, no-transform")
        self.send_header("X-Accel-Buffering", "no")
        self.send_header("Connection", "close")
        self.send_header("X-Agent-Slack-Api-Version", API_VERSION)
        self.end_headers()
        connected = True
        try:
            for event in events:
                if not connected:
                    continue
                raw = (json.dumps(event, ensure_ascii=False) + "\n").encode("utf-8")
                try:
                    self.wfile.write(raw)
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    # Agent runs are backend jobs. Finish consuming the generator so
                    # replies persist even when the initiating client disconnects.
                    connected = False
        except Exception as exc:
            error = {"type": "error", "message": str(exc)}
            if connected:
                try:
                    self.wfile.write((json.dumps(error) + "\n").encode("utf-8"))
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    pass
        finally:
            self.close_connection = True

    def _serve_static(self, path: str) -> None:
        rel = "index.html" if path in {"/", ""} else path.lstrip("/")
        file_path = (self.manager.static_root / rel).resolve()
        if not str(file_path).startswith(str(self.manager.static_root.resolve())) or not file_path.exists():
            file_path = self.manager.static_root / "index.html"
        return self._serve_file(file_path)

    def _serve_file(self, file_path: Path) -> None:
        content = file_path.read_bytes()
        mime, _enc = mimetypes.guess_type(str(file_path))
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", mime or "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("X-Agent-Slack-Api-Version", API_VERSION)
        self.end_headers()
        self.wfile.write(content)


class AgentSlackHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True


def run_server(project_root: Path | None, host: str, port: int, data_root: Path | None = None) -> None:
    app_root = Path(__file__).resolve().parent.parent
    manager = AgentServerManager(
        app_root=app_root,
        data_root=data_root or app_root / "data",
        initial_project_root=project_root,
    )
    _Handler.manager = manager
    server = AgentSlackHTTPServer((host, port), _Handler)
    print(f"Agent Slack running at http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
