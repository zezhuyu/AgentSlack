from __future__ import annotations

import json
import mimetypes
import re
import sys
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse
from uuid import uuid4

from .realtime.protocol import ConnectionClosed, WebSocketConnection, handshake_response
from .realtime.session import VoiceSession
from .mcp_server import AgentSlackMCP, handle_jsonrpc
from .servers import AgentServerManager


API_VERSION = "1"

# mimetypes.guess_type() doesn't know these on many platforms (notably .glb/.gltf,
# never registered in the stdlib db) - without this fallback, _serve_file() sends
# them as "text/html", which breaks <model-viewer>/<audio> entirely.
EXTENSION_CONTENT_TYPES = {
    ".glb": "model/gltf-binary", ".gltf": "model/gltf+json",
    ".obj": "model/obj", ".blend": "model/blender",
    ".mp3": "audio/mpeg", ".wav": "audio/wav", ".m4a": "audio/mp4", ".ogg": "audio/ogg",
    ".mid": "audio/midi", ".midi": "audio/midi",
    ".webm": "video/webm", ".mov": "video/quicktime",
}


def _guess_content_type(file_path: Path) -> str | None:
    mime, _enc = mimetypes.guess_type(str(file_path))
    return mime or EXTENSION_CONTENT_TYPES.get(file_path.suffix.lower())


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
        "/uploads": {
            "post": {
                "summary": "Upload one file for a later chat message",
                "description": "Raw request body with a percent-encoded X-Agent-Slack-Filename header.",
            }
        },
        "/uploads/{attachment_id}": {"get": {"summary": "Preview or download an uploaded file"}},
        "/voice/capabilities": {"get": {"summary": "Return local voice channel capabilities"}},
        "/voice/utterances": {"post": {"summary": "Submit a committed utterance through the shared agent"}},
        "/voice/transcribe": {"post": {"summary": "Transcribe audio using backend-local whisper.cpp"}},
        "/voice/synthesize": {"post": {"summary": "Synthesize speech using backend-local TTS"}},
        "/voice/sessions": {"post": {"summary": "Create an isolated voice conversation session"}},
        "/voice/turns": {"post": {"summary": "Process one conversational voice turn with backend ASR/TTS"}},
        "/chats/{chat_id}/questions/{question_id}/answer": {
            "post": {"summary": "Answer an agent-authored dynamic choice question"},
        },
        "/chats/{chat_id}/run-stream": {
            "post": {
                "summary": "Run agents and stream NDJSON events",
                "responses": {"200": {"description": "NDJSON event stream"}},
            }
        },
        "/runs/active": {
            "get": {"summary": "List active runs and tasks for reconnecting clients"},
        },
        "/runs/{run_id}/cancel": {
            "post": {"summary": "Stop an active agent-system run"},
        },
        "/runs/{run_id}/tasks/{task_id}/cancel": {
            "post": {"summary": "Stop one native task without stopping sibling tasks"},
        },
        "/chats/{chat_id}/meeting": {"post": {"summary": "Run a manual agent meeting"}},
        "/chats/{chat_id}/auto-meeting": {"post": {"summary": "Run an architecture-routed meeting"}},
        "/chat/sessions": {
            "get": {"summary": "List Chat mode sessions"},
            "post": {"summary": "Create a Chat mode session (read-only, no agent execution)"},
        },
        "/chat/sessions/{id}": {"get": {"summary": "Get a Chat mode session and its messages"}},
        "/chat/sessions/{id}/messages": {"post": {"summary": "Send a Chat mode message and stream NDJSON events"}},
        "/chat/sessions/{id}/cancel": {"post": {"summary": "Cancel a Chat mode response by response_id"}},
        "/chat/index/status": {"get": {"summary": "Local retrieval index status for the active server"}},
        "/chat/index/build": {"post": {"summary": "(Re)build the local retrieval index for the active server"}},
        "/chat/voice": {
            "get": {"summary": "Full-duplex Chat mode voice session (WebSocket upgrade)"},
        },
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
        if path == "/mcp":
            # MCP is stateless Streamable HTTP here and accepts JSON-RPC over
            # POST. Do not let discovery receive the web UI with a 200 response.
            self.send_response(HTTPStatus.METHOD_NOT_ALLOWED)
            self.send_header("Allow", "POST")
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if path == "/api/chat/voice" and self.headers.get("Upgrade", "").lower() == "websocket":
            return self._handle_voice_websocket()
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
        match = re.fullmatch(r"/api/uploads/([0-9a-f]{32})", path)
        if match:
            upload = self._active_app().get_upload_path(match.group(1))
            if upload is None:
                return self._json({"error": "attachment not found"}, status=404)
            file_path, metadata = upload
            return self._serve_file(
                file_path, filename=metadata.get("filename"), inline=True,
                content_type=metadata.get("content_type"),
            )
        match = re.fullmatch(r"/api/artifacts/([0-9a-f]{32})", path)
        if match:
            artifact = self._active_app().get_artifact_path(match.group(1))
            if artifact is None:
                return self._json({"error": "artifact not found"}, status=404)
            file_path, metadata = artifact
            return self._serve_file(
                file_path, filename=metadata.get("filename"), inline=True,
                content_type=metadata.get("content_type"),
            )
        match = re.fullmatch(r"/api/voice/speech/([0-9a-f]{32})", path)
        if match:
            speech_path = self._active_app().speech_path(match.group(1))
            if speech_path is None:
                return self._json({"error": "speech not found"}, status=404)
            return self._serve_file(speech_path, filename="response.wav", inline=True)
        if path == "/api/files":
            raw_path = (parse_qs(parsed.query).get("path") or [""])[0]
            file_path = self._active_app().resolve_workspace_file(raw_path)
            if file_path is None:
                return self._json({"error": "file not found"}, status=404)
            return self._serve_file(file_path, filename=file_path.name, inline=True)
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
        if path == "/api/runs/active":
            chat_id = (parse_qs(parsed.query).get("chat_id") or [None])[0]
            return self._json({"runs": app.list_active_runs(chat_id)})
        if path == "/api/voice/capabilities":
            return self._json(app.voice_capabilities())
        if path == "/api/chat/sessions":
            return self._json({"sessions": [session.to_dict() for session in app.chat_engine.list_sessions()]})
        if path == "/api/chat/index/status":
            return self._json(app.retrieval.status())
        match = re.fullmatch(r"/api/chat/sessions/([^/]+)", path)
        if match:
            session = app.chat_engine.get_session(match.group(1))
            if session is None:
                return self._json({"error": "chat session not found"}, status=404)
            return self._json(session.to_dict())
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
        if path == "/mcp":
            try:
                request = self._read_json_body()
                response = handle_jsonrpc(AgentSlackMCP(self.manager), request)
            except Exception as exc:
                response = {"jsonrpc": "2.0", "id": None,
                            "error": {"code": -32000, "message": str(exc)}}
            if response is None:
                self.send_response(HTTPStatus.ACCEPTED)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            return self._json(response)
        if path == "/api/servers":
            payload = self._read_json_body()
            project_root = str(payload.get("project_root") or "").strip()
            if not project_root:
                return self._json({"error": "project_root is required"}, status=400)
            try:
                server = self.manager.add_server(
                Path(project_root),
                name=str(payload.get("name") or "") or None,
                logo_path=(Path(str(payload["logo_path"])) if payload.get("logo_path") else None),
                runner=(str(payload["runner"]) if "runner" in payload else None),
                model=(str(payload["model"]) if "model" in payload else None),
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
        if path == "/api/uploads":
            filename = unquote(self.headers.get("X-Agent-Slack-Filename", "")).strip()
            if not filename:
                return self._json({"error": "X-Agent-Slack-Filename is required"}, status=400)
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                return self._json({"error": "Invalid Content-Length"}, status=400)
            if length <= 0:
                return self._json({"error": "Upload is empty"}, status=400)
            if length > app.MAX_UPLOAD_BYTES:
                return self._json({"error": "Upload exceeds the 50 MB limit"}, status=413)
            try:
                upload = app.store_upload(
                    filename,
                    self.headers.get("Content-Type", "application/octet-stream"),
                    self.rfile.read(length),
                )
            except ValueError as exc:
                return self._json({"error": str(exc)}, status=400)
            return self._json(upload, status=201)
        if path == "/api/voice/transcribe":
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                return self._json({"error": "Invalid Content-Length"}, status=400)
            if length <= 0 or length > app.MAX_UPLOAD_BYTES:
                return self._json({"error": "Audio must be between 1 byte and 50 MB"}, status=413)
            try:
                text = app.voice.transcribe(
                    self.rfile.read(length),
                    Path(self.headers.get("X-Agent-Slack-Filename", "audio.wav")).suffix,
                )
            except (OSError, ValueError) as exc:
                return self._json({"error": str(exc)}, status=400)
            except Exception as exc:
                return self._json({"error": str(exc)}, status=503)
            return self._json({"text": text, "final": True})
        if path == "/api/voice/turns":
            chat_id = str(self.headers.get("X-Agent-Slack-Voice-Chat") or "")
            request_id = str(self.headers.get("Idempotency-Key") or uuid4().hex)
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                return self._json({"error": "Invalid Content-Length"}, status=400)
            if not chat_id or length <= 0 or length > app.MAX_UPLOAD_BYTES:
                return self._json({"error": "voice chat and audio body are required"}, status=400)
            try:
                events = app.stream_voice_turn(
                    chat_id,
                    self.rfile.read(length),
                    Path(self.headers.get("X-Agent-Slack-Filename", "voice.wav")).suffix,
                    request_id,
                )
            except (KeyError, ValueError) as exc:
                return self._json({"error": str(exc)}, status=400)
            return self._ndjson(events)
        payload = self._read_json_body()
        if path == "/api/voice/sessions":
            try:
                return self._json(app.create_voice_session(str(payload.get("source_chat_id") or "")), status=201)
            except (KeyError, ValueError) as exc:
                return self._json({"error": str(exc)}, status=400)
        if path == "/api/voice/synthesize":
            try:
                audio = app.voice.synthesize(str(payload.get("text") or ""), payload.get("voice"))
            except Exception as exc:
                return self._json({"error": str(exc)}, status=503)
            return self._audio(audio, "audio/wav")
        if path == "/api/agents/discover":
            app.reload_host_configuration()
            return self._json({"agents": app.refresh_agents()})
        if path == "/api/chat/index/build":
            return self._json({"files_indexed": app.retrieval.ensure_index()})
        if path == "/api/chat/sessions":
            session = app.chat_engine.create_session(
                self.manager.active_server_id or "", title=str(payload.get("title") or "New chat"),
            )
            return self._json(session.to_dict(), status=201)
        match = re.fullmatch(r"/api/chat/sessions/([^/]+)/messages", path)
        if match:
            text = str(payload.get("text") or payload.get("body") or "").strip()
            modality = str(payload.get("modality") or "text")
            attachments = [item for item in (payload.get("attachments") or []) if isinstance(item, dict)]
            if not text:
                return self._json({"error": "text is required"}, status=400)
            try:
                events = app.chat_engine.stream_message(match.group(1), text, modality=modality, attachments=attachments)
            except KeyError as exc:
                return self._json({"error": str(exc)}, status=404)
            return self._ndjson(events)
        match = re.fullmatch(r"/api/chat/sessions/([^/]+)/cancel", path)
        if match:
            response_id = str(payload.get("response_id") or "")
            if not response_id:
                return self._json({"error": "response_id is required"}, status=400)
            app.chat_engine.cancel(response_id)
            return self._json({"accepted": True, "response_id": response_id}, status=202)
        if path == "/api/chats":
            chat = app.create_chat(
                title=str(payload.get("title") or "Untitled chat"),
                member_ids=list(payload.get("member_ids") or []),
                kind=str(payload.get("kind") or "group"),
            )
            return self._json(chat, status=201)
        match = re.fullmatch(r"/api/chats/([^/]+)/messages", path)
        if match:
            try:
                chat = app.add_user_message(
                    match.group(1),
                    str(payload.get("body") or ""),
                    attachment_ids=list(payload.get("attachment_ids") or []),
                    idempotency_key=str(payload.get("idempotency_key") or self.headers.get("Idempotency-Key") or ""),
                )
            except (KeyError, ValueError) as exc:
                return self._json({"error": str(exc).strip("'")}, status=400)
            return self._json(chat, status=201)
        if path == "/api/voice/utterances":
            chat_id = str(payload.get("chat_id") or "")
            text = str(payload.get("text") or payload.get("body") or "").strip()
            request_id = str(payload.get("request_id") or self.headers.get("Idempotency-Key") or "").strip()
            if not chat_id or not text:
                return self._json({"error": "chat_id and text are required"}, status=400)
            try:
                chat, is_new = app.submit_voice_utterance(chat_id, text, request_id)
                if not is_new:
                    existing = app.find_message_by_idempotency(chat_id, request_id)
                    return self._json({"accepted": True, "duplicate": True, "message_id": existing.get("message_id") if existing else None})
                events = app.stream_run(
                    chat_id=chat_id,
                    mode="respond",
                    objective=payload.get("objective"),
                    agent_ids=list(payload.get("agent_ids") or []),
                    lead_agent_id=payload.get("lead_agent_id"),
                )
            except (KeyError, ValueError) as exc:
                return self._json({"error": str(exc)}, status=400)
            return self._ndjson(events)
        match = re.fullmatch(r"/api/chats/([^/]+)/questions/([^/]+)/answer", path)
        if match:
            try:
                chat = app.answer_question(
                    match.group(1),
                    match.group(2),
                    list(payload.get("option_ids") or []),
                )
            except KeyError as exc:
                return self._json({"error": str(exc).strip("'")}, status=404)
            except ValueError as exc:
                return self._json({"error": str(exc)}, status=400)
            return self._json(chat)
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
                runner=(str(payload["runner"]) if "runner" in payload else None),
                model=(str(payload["model"]) if "model" in payload else None),
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
        self.send_header(
            "Access-Control-Allow-Headers",
            "Content-Type, X-Agent-Slack-Server, X-Agent-Slack-Filename",
        )
        self.send_header("X-Agent-Slack-Api-Version", API_VERSION)
        self.end_headers()

    def log_message(self, format: str, *args) -> None:  # noqa: A003
        return

    def _active_app(self):
        requested_server_id = self.headers.get("X-Agent-Slack-Server", "").strip()
        if requested_server_id:
            return self.manager.app_for(requested_server_id)
        return self.manager.active_app()

    def _handle_voice_websocket(self) -> None:
        client_key = self.headers.get("Sec-WebSocket-Key", "").strip()
        if not client_key:
            self.send_response(HTTPStatus.BAD_REQUEST)
            self.end_headers()
            return
        try:
            app = self._active_app()
        except (KeyError, LookupError, ValueError):
            self.send_response(HTTPStatus.CONFLICT)
            self.end_headers()
            return
        self.connection.sendall(handshake_response(client_key))
        self.close_connection = True
        connection = WebSocketConnection(self.connection)
        session = VoiceSession(connection, app.chat_engine, app.voice, self.manager.active_server_id or "")
        try:
            while True:
                frame = connection.recv_message()
                if frame.opcode == 0x1:  # text
                    try:
                        event = json.loads(frame.payload.decode("utf-8"))
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        continue
                    if not session.handle_control(event):
                        break
                elif frame.opcode == 0x2:  # binary audio chunk
                    session.handle_audio_chunk(frame.payload)
        except ConnectionClosed:
            pass
        finally:
            connection.close()

    def _read_json_body(self) -> dict:
        if self.headers.get("Transfer-Encoding", "").lower() == "chunked":
            chunks = bytearray()
            while True:
                line = self.rfile.readline(65536)
                if not line:
                    break
                try:
                    size = int(line.split(b";", 1)[0].strip(), 16)
                except ValueError:
                    return {}
                if size == 0:
                    # Consume the trailing headers, if any.
                    while self.rfile.readline(65536).strip():
                        pass
                    break
                chunks.extend(self.rfile.read(size))
                self.rfile.readline(2)  # CRLF after the chunk body.
            body = bytes(chunks)
        else:
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                return {}
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

    def _audio(self, content: bytes, content_type: str) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Agent-Slack-Api-Version", API_VERSION)
        self.end_headers()
        self.wfile.write(content)

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

    def _serve_file(
        self,
        file_path: Path,
        filename: str | None = None,
        inline: bool = False,
        content_type: str | None = None,
    ) -> None:
        content = file_path.read_bytes()
        total = len(content)
        mime = content_type or _guess_content_type(file_path) or "text/html; charset=utf-8"

        # Safari refuses to play <audio>/<video> (and model-viewer can stall) unless
        # the server honors Range requests, so parse a single "bytes=start-end" spec.
        start, end, status = 0, total - 1, HTTPStatus.OK
        range_header = self.headers.get("Range", "")
        if range_header.startswith("bytes="):
            spec = range_header[len("bytes="):].split(",")[0]
            range_start, _, range_end = spec.partition("-")
            try:
                start = int(range_start) if range_start else 0
                end = int(range_end) if range_end else total - 1
            except ValueError:
                start, end = 0, total - 1
            if total and 0 <= start <= end < total:
                status = HTTPStatus.PARTIAL_CONTENT
            else:
                start, end = 0, total - 1

        body = content[start : end + 1] if total else b""
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Accept-Ranges", "bytes")
        if status == HTTPStatus.PARTIAL_CONTENT:
            self.send_header("Content-Range", f"bytes {start}-{end}/{total}")
        if filename:
            disposition = "inline" if inline else "attachment"
            self.send_header("Content-Disposition", f'{disposition}; filename="{str(filename).replace(chr(34), chr(39))}"')
        self.send_header("X-Agent-Slack-Api-Version", API_VERSION)
        self.end_headers()
        self.wfile.write(body)


class AgentSlackHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True


def run_server(project_root: Path | None, host: str, port: int, data_root: Path | None = None) -> None:
    # PyInstaller extracts bundled static assets beside the frozen executable
    # under _MEIPASS; source runs keep the repository root as before.
    app_root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
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
