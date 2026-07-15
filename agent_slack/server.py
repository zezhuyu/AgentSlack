from __future__ import annotations

import json
import mimetypes
import re
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from .servers import AgentServerManager


class _Handler(BaseHTTPRequestHandler):
    manager: AgentServerManager

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/api/health":
            active = self.manager.active_summary()
            architecture = self._active_app().architecture_summary() if active else None
            return self._json(
                {
                    "ok": True,
                    "workspace": active["name"] if active else None,
                    "active_server_id": self.manager.active_server_id,
                    "servers": self.manager.list_servers(),
                    "architecture": architecture,
                }
            )
        if parsed.path == "/api/servers":
            return self._json(
                {
                    "servers": self.manager.list_servers(),
                    "active_server_id": self.manager.active_server_id,
                }
            )
        match = re.fullmatch(r"/api/servers/([^/]+)/logo", parsed.path)
        if match:
            try:
                logo_path = self.manager.logo_path(match.group(1))
            except KeyError:
                return self._json({"error": "server not found"}, status=404)
            if logo_path is None:
                return self._json({"error": "server logo not found"}, status=404)
            return self._serve_file(logo_path)
        if parsed.path.startswith("/api/") and self.manager.active_server_id is None:
            return self._json({"error": "No agent server is configured"}, status=409)
        if parsed.path == "/api/agents":
            return self._json({"agents": self._active_app().list_agents()})
        if parsed.path == "/api/chats":
            return self._json({"chats": self._active_app().list_chats()})
        match = re.fullmatch(r"/api/chats/([^/]+)", parsed.path)
        if match:
            chat = self._active_app().get_chat(match.group(1))
            if chat is None:
                return self._json({"error": "chat not found"}, status=404)
            return self._json(chat)
        return self._serve_static(parsed.path)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        payload = self._read_json_body()
        if parsed.path == "/api/servers":
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
        match = re.fullmatch(r"/api/servers/([^/]+)/activate", parsed.path)
        if match:
            try:
                server = self.manager.activate(match.group(1))
            except KeyError as exc:
                return self._json({"error": str(exc)}, status=404)
            except ValueError as exc:
                return self._json({"error": str(exc)}, status=400)
            return self._json(server)
        if parsed.path.startswith("/api/") and self.manager.active_server_id is None:
            return self._json({"error": "No agent server is configured"}, status=409)
        if parsed.path == "/api/agents/discover":
            return self._json({"agents": self._active_app().refresh_agents()})
        if parsed.path == "/api/chats":
            chat = self._active_app().create_chat(
                title=str(payload.get("title") or "Untitled chat"),
                member_ids=list(payload.get("member_ids") or []),
                kind=str(payload.get("kind") or "group"),
            )
            return self._json(chat, status=201)
        match = re.fullmatch(r"/api/chats/([^/]+)/messages", parsed.path)
        if match:
            chat = self._active_app().add_user_message(match.group(1), str(payload.get("body") or ""))
            return self._json(chat, status=201)
        match = re.fullmatch(r"/api/chats/([^/]+)/run-stream", parsed.path)
        if match:
            try:
                events = self._active_app().stream_run(
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
        match = re.fullmatch(r"/api/chats/([^/]+)/respond", parsed.path)
        if match:
            chat = self._active_app().run_agents(
                match.group(1),
                agent_ids=list(payload.get("agent_ids") or []),
                objective=payload.get("objective"),
            )
            return self._json(chat)
        match = re.fullmatch(r"/api/chats/([^/]+)/meeting", parsed.path)
        if match:
            lead_agent_id = str(payload.get("lead_agent_id") or "")
            objective = str(payload.get("objective") or "")
            if not lead_agent_id or not objective:
                return self._json({"error": "lead_agent_id and objective are required"}, status=400)
            chat = self._active_app().create_meeting(
                chat_id=match.group(1),
                lead_agent_id=lead_agent_id,
                participant_ids=list(payload.get("participant_ids") or []),
                objective=objective,
                auto_run=bool(payload.get("auto_run", True)),
            )
            return self._json(chat)
        match = re.fullmatch(r"/api/chats/([^/]+)/auto-meeting", parsed.path)
        if match:
            lead_agent_id = str(payload.get("lead_agent_id") or "")
            objective = str(payload.get("objective") or "")
            if not lead_agent_id or not objective:
                return self._json({"error": "lead_agent_id and objective are required"}, status=400)
            chat = self._active_app().auto_meeting(match.group(1), lead_agent_id=lead_agent_id, objective=objective)
            return self._json(chat)
        return self._json({"error": "not found"}, status=404)

    def do_PATCH(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        payload = self._read_json_body()
        match = re.fullmatch(r"/api/servers/([^/]+)", parsed.path)
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
        self.end_headers()
        self.wfile.write(raw)

    def _ndjson(self, events) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
        self.send_header("Cache-Control", "no-cache, no-transform")
        self.send_header("X-Accel-Buffering", "no")
        self.send_header("Connection", "close")
        self.end_headers()
        try:
            for event in events:
                raw = (json.dumps(event, ensure_ascii=False) + "\n").encode("utf-8")
                self.wfile.write(raw)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as exc:
            error = {"type": "error", "message": str(exc)}
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
        self.end_headers()
        self.wfile.write(content)


def run_server(project_root: Path | None, host: str, port: int, data_root: Path | None = None) -> None:
    app_root = Path(__file__).resolve().parent.parent
    manager = AgentServerManager(
        app_root=app_root,
        data_root=data_root or app_root / "data",
        initial_project_root=project_root,
    )
    _Handler.manager = manager
    server = ThreadingHTTPServer((host, port), _Handler)
    print(f"Agent Slack running at http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
