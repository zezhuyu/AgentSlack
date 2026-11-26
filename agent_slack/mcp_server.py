"""Small MCP-compatible stdio adapter for AgentSlack.

Project tools are workspace scoped; this server never exposes a shell or
arbitrary file access.
"""
from __future__ import annotations

import json
import sys
import argparse
import base64
from hashlib import sha256
from datetime import datetime, timezone
import re
import os
import tempfile
import threading
import uuid
from pathlib import Path
from typing import Any, Callable

from .servers import AgentServerManager
from .titles import short_title
from .storage import utc_now


def _read_session_records(path: Path) -> dict[str, dict[str, Any]]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_session_records(path: Path, records: dict[str, dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".mcp_sessions-", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(records, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


class AgentSlackMCP:
    MAX_MEMORY_RESULT_CHARS = 1200
    MAX_STATUS_TASKS = 50
    MAX_MEMORY_RESULTS = 8
    _session_locks: dict[str, threading.RLock] = {}
    _session_locks_guard = threading.Lock()

    def __init__(self, manager: AgentServerManager):
        self.manager = manager
        self._call_context = threading.local()

    @staticmethod
    def _compact_agent(agent: dict[str, Any]) -> dict[str, Any]:
        """Keep MCP status responses small; system prompts stay local."""
        allowed = ("agent_id", "name", "title", "summary", "source_path", "group", "kind", "tools")
        return {key: agent[key] for key in allowed if key in agent}

    @classmethod
    def _compact_task(cls, task: dict[str, Any]) -> dict[str, Any]:
        compact = dict(task)
        if isinstance(compact.get("instruction"), str):
            compact["instruction"] = compact["instruction"][:cls.MAX_MEMORY_RESULT_CHARS]
        if isinstance(compact.get("result"), str):
            compact["result"] = compact["result"][:cls.MAX_MEMORY_RESULT_CHARS]
        elif compact.get("result") is not None:
            try:
                encoded = json.dumps(compact["result"], ensure_ascii=False)
                compact["result"] = encoded[:cls.MAX_MEMORY_RESULT_CHARS]
            except (TypeError, ValueError):
                compact["result"] = str(compact["result"])[:cls.MAX_MEMORY_RESULT_CHARS]
        return compact

    def _app(self, workspace_key: str):
        if not str(workspace_key or "").strip():
            raise ValueError("workspace_key is required")
        return self.manager.app_for_workspace(workspace_key)

    def get_project_status(self, workspace_key: str) -> dict[str, Any]:
        app = self._app(workspace_key)
        active_runs = []
        for run in app.list_active_runs():
            compact_run = {key: value for key, value in run.items() if key != "tasks"}
            compact_run["tasks"] = []
            for task in list(run.get("tasks") or [])[:self.MAX_STATUS_TASKS]:
                compact_task = dict(task)
                if isinstance(compact_task.get("text"), str):
                    compact_task["text"] = compact_task["text"][-self.MAX_MEMORY_RESULT_CHARS:]
                compact_run["tasks"].append(compact_task)
            active_runs.append(compact_run)
        return {
            "workspace_key": app.workspace_key,
            "architecture": app.architecture_summary(),
            "agents": [self._compact_agent(agent) for agent in app.list_agents()],
            "active_runs": active_runs,
            "tasks": [self._compact_task(task) for task in app.list_tasks()[:self.MAX_STATUS_TASKS]],
        }

    def get_agent_status(self, workspace_key: str, agent_id: str | None = None) -> Any:
        app = self._app(workspace_key)
        if agent_id:
            agent = app.get_agent(agent_id)
            if agent is None:
                raise KeyError(f"Agent not found: {agent_id}")
            return self._compact_agent(agent)
        return [self._compact_agent(agent) for agent in app.list_agents()]

    def search_memory(self, workspace_key: str, query: str, sources: list[str] | None = None,
                      limit: int = 10) -> list[dict[str, Any]]:
        safe_limit = min(max(1, int(limit or 10)), self.MAX_MEMORY_RESULTS)
        return self._app(workspace_key).search_memory(query, sources=sources, limit=safe_limit)

    def get_project_memory(self, workspace_key: str) -> dict[str, Any]:
        return self._app(workspace_key).get_project_memory()

    def append_project_decision(self, workspace_key: str, content: str, rationale: str | None = None,
                                source: str | None = None) -> dict[str, Any]:
        return self._app(workspace_key).append_project_decision(content, rationale, source or "chatgpt")

    def append_project_insight(self, workspace_key: str, content: str, insight_type: str = "general",
                               source: str | None = None) -> dict[str, Any]:
        return self._app(workspace_key).append_project_insight(content, insight_type, source or "chatgpt")

    def update_project_summary(self, workspace_key: str, section: str, content: str,
                               source: str | None = None) -> dict[str, Any]:
        return self._app(workspace_key).update_project_summary(section, content, source or "chatgpt")

    def list_project_files(self, workspace_key: str) -> list[dict[str, Any]]:
        return self._app(workspace_key).list_project_files()

    def search_project_files(self, workspace_key: str, query: str) -> list[dict[str, Any]]:
        return self._app(workspace_key).search_project_files(query)

    def read_project_file(self, workspace_key: str, path: str) -> dict[str, Any]:
        return self._app(workspace_key).read_project_file(path)

    def list_tasks(self, workspace_key: str) -> list[dict[str, Any]]:
        return self._app(workspace_key).list_tasks()

    def get_task(self, workspace_key: str, task_id: str) -> dict[str, Any] | None:
        return self._app(workspace_key).get_task(task_id)

    def create_task(self, workspace_key: str, agent_id: str, instruction: str,
                    session_id: str | None = None) -> dict[str, Any]:
        return self.create_session_task(workspace_key, agent_id, instruction, session_id, getattr(self._call_context, "meta", None))

    def create_session_task(self, workspace_key: str, agent_id: str, instruction: str,
                            session_id: str | None, meta: dict[str, Any] | None,
                            skill_name: str | None = None) -> dict[str, Any]:
        app = self._app(workspace_key)
        explicit = session_id is not None
        if explicit and (not isinstance(session_id, str) or not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", session_id)):
            raise ValueError("session_id must be 1-128 characters using letters, digits, '.', '_', ':', or '-'")
        source = "explicit"
        conversation = session_id or ""
        if not explicit:
            meta = meta if isinstance(meta, dict) else {}
            for key, label in (("openai/session", "chatgpt"), ("anthropic/session", "claude"),
                               ("claude/conversation_id", "claude"), ("conversationId", "claude"),
                               ("conversation_id", "claude")):
                candidate = meta.get(key)
                if isinstance(candidate, str) and candidate:
                    source, conversation = label, candidate
                    break
        path = app.data_root / "mcp_sessions.json"
        with self._session_locks_guard:
            lock = self._session_locks.setdefault(str(path), threading.RLock())
        mapping_key = sha256(f"{source}|{conversation}|{agent_id}".encode()).hexdigest() if conversation else ""
        with lock:
            records = _read_session_records(path)
            if explicit and session_id.startswith("as_"):
                if not re.fullmatch(r"as_[0-9a-f]{16}", session_id):
                    raise ValueError("Invalid session_id token")
                found = next(((key, value) for key, value in records.items()
                              if value.get("token") == session_id and value.get("agent_id") == agent_id), None)
                if found is None:
                    raise ValueError("Unknown session_id token")
                mapping_key, record = found
                conversation = record.get("conversation_id_hash", session_id)
                source = record.get("source", source)
            record = records.get(mapping_key) if mapping_key else None
            chat = app.get_chat(record.get("chat_id")) if record else None
            reused = bool(chat and agent_id in (chat.get("member_ids") or []))
            if not reused:
                agent = app.get_agent(agent_id)
                if agent is None:
                    raise ValueError(f"Agent not found: {agent_id}")
                label = str(agent.get("name") or agent.get("title") or agent_id)
                title_source = {"chatgpt": "ChatGPT", "claude": "Claude", "explicit": "Session"}[source]
                chat = app.create_chat(f"{title_source} · {short_title(instruction) or label}", [agent_id], kind="task")
                if not mapping_key:
                    conversation = uuid.uuid4().hex
                    mapping_key = sha256(f"explicit|{conversation}|{agent_id}".encode()).hexdigest()
                token = "as_" + uuid.uuid4().hex[:16]
                record = {"chat_id": chat["chat_id"], "agent_id": agent_id, "source": source,
                          "conversation_id_hash": sha256(conversation.encode()).hexdigest(),
                          "created": utc_now(), "last_used": utc_now(), "task_count": 0, "token": token}
                records[mapping_key] = record
            else:
                token = record.get("token") or ("as_" + uuid.uuid4().hex[:16])
            record["last_used"] = utc_now()
            record["task_count"] = int(record.get("task_count", 0)) + 1
            # Explicit tokens resolve to the same record on later calls.
            record["token"] = token
            records[mapping_key] = record
            if len(records) > 500:
                records = dict(sorted(records.items(), key=lambda pair: pair[1].get("last_used", ""))[-500:])
            _write_session_records(path, records)
        instruction_text = instruction
        if skill_name:
            skill = resolve_project_skill(app.project_root, skill_name)
            instruction_text = (f"Use the project skill {skill['skill_name']} from {skill['path']}. Read its complete SKILL.md before acting.\n\nUser request:\n{instruction}")
        task = app.create_task(agent_id, instruction_text, source=source, chat_id=chat["chat_id"],
                               extra={"session_id": token, "session_reused": reused,
                                      **({"skill_name": skill_name} if skill_name else {})})
        return {**task, "session_id": token, "chat_id": chat["chat_id"], "session_reused": reused}

    def cancel_task(self, workspace_key: str, task_id: str) -> dict[str, Any]:
        return self._app(workspace_key).cancel_task_record(task_id)

    def list_task_artifacts(self, workspace_key: str, task_id: str) -> list[dict[str, Any]]:
        return self._app(workspace_key).list_task_artifacts(task_id)

    def get_task_artifact(self, workspace_key: str, task_id: str, artifact_id: str) -> dict[str, Any]:
        app = self._app(workspace_key)
        path, metadata = app.get_task_artifact(task_id, artifact_id)
        max_resource_bytes = 25 * 1024 * 1024
        if path.stat().st_size > max_resource_bytes:
            raise ValueError("Artifact is too large for an MCP file response; use the local AgentSlack app")
        return {
            "uri": f"agent-slack://{app.workspace_key}/tasks/{task_id}/artifacts/{artifact_id}",
            "mimeType": metadata.get("content_type", "application/octet-stream"),
            "blob": base64.b64encode(path.read_bytes()).decode("ascii"),
            "name": metadata.get("filename", path.name),
            "size": path.stat().st_size,
        }

    def download_task_artifact(self, workspace_key: str, task_id: str, artifact_id: str) -> dict[str, Any]:
        """Return a materializable MCP file resource for any safe artifact type.

        ``file_id`` is the stable AgentSlack artifact ID. ChatGPT uses the
        embedded resource or resource URI to fetch the bytes through MCP and
        can then materialize the file in its conversation/file surface. The
        artifact's extension is deliberately not used as an allow-list: the
        project-scoped storage/security checks have already rejected secrets
        and files outside the task artifact directory.
        """
        app = self._app(workspace_key)
        path, metadata = app.get_task_artifact(task_id, artifact_id)
        max_resource_bytes = 25 * 1024 * 1024
        if path.stat().st_size > max_resource_bytes:
            raise ValueError("Artifact is too large for an MCP file response; use the local AgentSlack app")
        uri = f"agent-slack://{app.workspace_key}/tasks/{task_id}/artifacts/{artifact_id}"
        return {
            "file_uri": {
                "download_url": uri,
                "file_id": artifact_id,
                "mime_type": metadata.get("content_type", "application/octet-stream"),
                "file_name": metadata.get("filename", path.name),
            },
            "uri": uri,
            "name": metadata.get("filename", path.name),
            "mimeType": metadata.get("content_type", "application/octet-stream"),
            "size": path.stat().st_size,
            # Kept out of structuredContent below; this is the actual binary
            # payload for MCP hosts that cannot dereference agent-slack://.
            "_blob": base64.b64encode(path.read_bytes()).decode("ascii"),
        }

    @staticmethod
    def _artifact_content(value: dict[str, Any]) -> list[dict[str, Any]]:
        """Build MCP content that works for binary and human-readable files.

        The embedded ``resource`` is the canonical download representation for
        every MIME type, including OBJ/BLEND/PDF/JSON/Markdown/video and
        unknown binary formats. Native image/audio blocks are added as hints so
        clients that support inline media can preview those files without
        sacrificing the generic downloadable resource.
        """
        mime_type = value["mimeType"]
        blob = value["_blob"]
        content: list[dict[str, Any]] = [{
            "type": "resource",
            "resource": {"uri": value["uri"], "mimeType": mime_type, "blob": blob},
        }]
        if mime_type.startswith("image/"):
            content.insert(0, {"type": "image", "data": blob, "mimeType": mime_type})
        elif mime_type.startswith("audio/"):
            content.insert(0, {"type": "audio", "data": blob, "mimeType": mime_type})
        content.append({
            "type": "resource_link", "uri": value["uri"], "name": value["name"],
            "mimeType": mime_type, "size": value["size"],
        })
        return content

    def read_resource(self, uri: str) -> dict[str, Any]:
        """Read only an AgentSlack resource URI previously issued by this server."""
        match = re.fullmatch(r"agent-slack://([^/]+)/tasks/([^/]+)/artifacts/([0-9a-f]{32})", uri)
        if not match:
            raise ValueError("Unsupported or invalid AgentSlack resource URI")
        workspace_key, task_id, artifact_id = match.groups()
        return self.get_task_artifact(workspace_key, task_id, artifact_id)

    def list_project_skills(self, workspace_key: str) -> list[dict[str, Any]]:
        return self._app(workspace_key).list_project_skills()

    def run_project_skill(self, workspace_key: str, agent_id: str, skill_name: str,
                          instruction: str, session_id: str | None = None) -> dict[str, Any]:
        return self.create_session_task(workspace_key, agent_id, instruction, session_id,
                                        getattr(self._call_context, "meta", None), skill_name)

    TOOLS: dict[str, Callable[..., Any]] = {
        "get_project_status": get_project_status,
        "get_agent_status": get_agent_status,
        "search_memory": search_memory,
        "get_project_memory": get_project_memory,
        "append_project_decision": append_project_decision,
        "append_project_insight": append_project_insight,
        "update_project_summary": update_project_summary,
        "list_project_files": list_project_files,
        "search_project_files": search_project_files,
        "read_project_file": read_project_file,
        "list_tasks": list_tasks,
        "get_task": get_task,
        "create_task": create_task,
        "cancel_task": cancel_task,
        "list_task_artifacts": list_task_artifacts,
        "get_task_artifact": get_task_artifact,
        "download_task_artifact": download_task_artifact,
        "list_project_skills": list_project_skills,
        "run_project_skill": run_project_skill,
    }

    def call(self, name: str, arguments: dict[str, Any], meta: dict[str, Any] | None = None) -> Any:
        method = self.TOOLS.get(name)
        if method is None:
            raise KeyError(f"Unknown MCP tool: {name}")
        if name in {"create_task", "run_project_skill"}:
            self._call_context.meta = meta or {}
            try:
                return method(self, **arguments)
            finally:
                self._call_context.meta = {}
        arguments = dict(arguments)
        arguments.pop("session_id", None)
        return method(self, **arguments)


def _tool_definitions() -> list[dict[str, Any]]:
    fields = {
        "get_project_status": {},
        "get_agent_status": {"agent_id": {"type": "string"}},
        "search_memory": {"query": {"type": "string"}, "sources": {"type": "array", "items": {"type": "string"}}, "limit": {"type": "integer"}},
        "get_project_memory": {},
        "append_project_decision": {"content": {"type": "string"}, "rationale": {"type": "string"}, "source": {"type": "string"}},
        "append_project_insight": {"content": {"type": "string"}, "insight_type": {"type": "string"}, "source": {"type": "string"}},
        "update_project_summary": {"section": {"type": "string"}, "content": {"type": "string"}, "source": {"type": "string"}},
        "list_project_files": {},
        "search_project_files": {"query": {"type": "string"}},
        "read_project_file": {"path": {"type": "string"}},
        "list_tasks": {},
        "get_task": {"task_id": {"type": "string"}},
        "create_task": {"agent_id": {"type": "string"}, "instruction": {"type": "string"}, "session_id": {"type": "string", "maxLength": 128, "pattern": "^[A-Za-z0-9._:-]+$", "description": "Pass the session_id returned by earlier calls in this conversation to continue the same Agent Slack session."}},
        "cancel_task": {"task_id": {"type": "string"}},
        "list_task_artifacts": {"task_id": {"type": "string"}},
        "get_task_artifact": {"task_id": {"type": "string"}, "artifact_id": {"type": "string"}},
        "download_task_artifact": {"task_id": {"type": "string"}, "artifact_id": {"type": "string"}},
        "list_project_skills": {},
        "run_project_skill": {"agent_id": {"type": "string"}, "skill_name": {"type": "string"}, "instruction": {"type": "string"}, "session_id": {"type": "string", "maxLength": 128, "pattern": "^[A-Za-z0-9._:-]+$", "description": "Pass the session_id returned by earlier calls in this conversation to continue the same Agent Slack session."}},
    }
    required = {
        "get_agent_status": [], "search_memory": ["query"],
        "append_project_decision": ["content"], "append_project_insight": ["content"],
        "update_project_summary": ["section", "content"], "search_project_files": ["query"],
        "read_project_file": ["path"], "get_task": ["task_id"],
        "create_task": ["agent_id", "instruction"], "cancel_task": ["task_id"],
        "list_task_artifacts": ["task_id"], "get_task_artifact": ["task_id", "artifact_id"],
        "download_task_artifact": ["task_id", "artifact_id"],
        "run_project_skill": ["agent_id", "skill_name", "instruction"],
    }
    return [{"name": name, "description": (
                f"AgentSlack project-scoped {name}. Pass the session_id returned by earlier calls in this conversation to continue the same Agent Slack session."
                if name in {"create_task", "run_project_skill"} else f"AgentSlack project-scoped {name}"),
             "inputSchema": {"type": "object", "required": ["workspace_key", *required.get(name, [])],
                              "properties": {"workspace_key": {"type": "string"}, **fields[name]}}}
            for name in AgentSlackMCP.TOOLS]


def serve_stdio(adapter: AgentSlackMCP) -> None:
    for line in sys.stdin:
        try:
            request = json.loads(line)
            response = handle_jsonrpc(adapter, request)
        except Exception as exc:
            response = {"jsonrpc": "2.0", "id": request.get("id") if isinstance(request, dict) else None,
                        "error": {"code": -32000, "message": str(exc)}}
        if response is not None:
            sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
            sys.stdout.flush()


def handle_jsonrpc(adapter: AgentSlackMCP, request: dict[str, Any]) -> dict[str, Any] | None:
    method = request.get("method")
    # MCP notifications, including initialized, never receive a JSON-RPC reply.
    if method == "notifications/initialized" or "id" not in request:
        return None
    if method == "initialize":
        requested_version = str((request.get("params") or {}).get("protocolVersion") or "")
        supported_versions = {"2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25"}
        protocol_version = requested_version if requested_version in supported_versions else "2024-11-05"
        result = {"protocolVersion": protocol_version, "capabilities": {"tools": {}, "resources": {}},
                  "serverInfo": {"name": "agent-slack", "version": "1"}}
    elif method == "server/discover":
        # MCP 2026-07-28 uses a stateless discovery exchange instead of the
        # legacy initialize handshake. OpenAI Secure MCP Tunnel/ChatGPT sends
        # this probe before tools/list, so return the complete DiscoverResult
        # shape required by that protocol revision.
        result = {
            "resultType": "complete",
            "supportedVersions": ["2026-07-28", "2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05"],
            "capabilities": {"tools": {}, "resources": {}},
            "_meta": {"io.modelcontextprotocol/serverInfo": {"name": "agent-slack", "version": "1"}},
            "instructions": "AgentSlack provides project-scoped files, memory, status, and durable tasks. Every tool requires workspace_key.",
            "ttlMs": 0,
            "cacheScope": "private",
        }
    elif method == "tools/list":
        result = {"tools": _tool_definitions(), "ttlMs": 0, "cacheScope": "private"}
    elif method == "tools/call":
        params = request.get("params") or {}
        tool_name = str(params.get("name") or "")
        arguments = dict(params.get("arguments") or {})
        if tool_name not in {"create_task", "run_project_skill"}:
            arguments.pop("session_id", None)
        try:
            value = adapter.call(tool_name, arguments, meta=params.get("_meta") if isinstance(params.get("_meta"), dict) else None)
        except Exception as exc:
            return {"jsonrpc": "2.0", "id": request.get("id"),
                    "error": {"code": -32000, "message": str(exc)}}
        if tool_name == "get_task_artifact":
            result = {
                "content": [
                    {"type": "resource", "resource": value},
                    {"type": "resource_link", "uri": value["uri"], "name": value["name"],
                     "mimeType": value["mimeType"], "size": value["size"]},
                ],
                "structuredContent": {
                    "name": value.get("name"),
                    "size": value.get("size"),
                    "mimeType": value.get("mimeType"),
                    "uri": value.get("uri"),
                },
            }
        elif tool_name == "download_task_artifact":
            result = {
                "content": AgentSlackMCP._artifact_content(value),
                "structuredContent": {key: value[key] for key in ("file_uri", "uri", "name", "mimeType", "size")},
            }
        else:
            result = {"content": [{"type": "text", "text": json.dumps(value, ensure_ascii=False)}]}
    elif method == "resources/read":
        uri = str((request.get("params") or {}).get("uri") or "")
        resource = adapter.read_resource(uri)
        result = {"contents": [{"uri": resource["uri"], "mimeType": resource["mimeType"],
                                 "blob": resource["blob"]}]}
    else:
        return {"jsonrpc": "2.0", "id": request.get("id"),
                "error": {"code": -32601, "message": f"Method not found: {method}"}}
    return {"jsonrpc": "2.0", "id": request.get("id"), "result": result}


def main() -> None:
    parser = argparse.ArgumentParser(description="AgentSlack project-scoped MCP server")
    parser.add_argument("--data-root", required=True)
    parser.add_argument("--app-root", default=str(Path(__file__).resolve().parent.parent))
    args = parser.parse_args()
    manager = AgentServerManager(Path(args.app_root), Path(args.data_root))
    serve_stdio(AgentSlackMCP(manager))


if __name__ == "__main__":
    main()
