"""Small MCP-compatible stdio adapter for AgentSlack.

The adapter intentionally exposes project-scoped read/context tools and durable
task submission only. It never exposes a shell, arbitrary file access, or writes.
"""
from __future__ import annotations

import json
import sys
import argparse
import base64
import re
from pathlib import Path
from typing import Any, Callable

from .servers import AgentServerManager


class AgentSlackMCP:
    def __init__(self, manager: AgentServerManager):
        self.manager = manager

    def _app(self, workspace_key: str):
        if not str(workspace_key or "").strip():
            raise ValueError("workspace_key is required")
        return self.manager.app_for_workspace(workspace_key)

    def get_project_status(self, workspace_key: str) -> dict[str, Any]:
        app = self._app(workspace_key)
        return {"workspace_key": app.workspace_key, "architecture": app.architecture_summary(),
                "agents": app.list_agents(), "active_runs": app.list_active_runs(), "tasks": app.list_tasks()}

    def get_agent_status(self, workspace_key: str, agent_id: str | None = None) -> Any:
        app = self._app(workspace_key)
        if agent_id:
            agent = app.get_agent(agent_id)
            if agent is None:
                raise KeyError(f"Agent not found: {agent_id}")
            return agent
        return app.list_agents()

    def search_memory(self, workspace_key: str, query: str, sources: list[str] | None = None,
                      limit: int = 10) -> list[dict[str, Any]]:
        return self._app(workspace_key).search_memory(query, sources=sources, limit=limit)

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

    def create_task(self, workspace_key: str, agent_id: str, instruction: str) -> dict[str, Any]:
        return self._app(workspace_key).create_task(agent_id, instruction, source="chatgpt")

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
                          instruction: str) -> dict[str, Any]:
        return self._app(workspace_key).run_project_skill(skill_name, instruction, agent_id)

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

    def call(self, name: str, arguments: dict[str, Any]) -> Any:
        method = self.TOOLS.get(name)
        if method is None:
            raise KeyError(f"Unknown MCP tool: {name}")
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
        "create_task": {"agent_id": {"type": "string"}, "instruction": {"type": "string"}},
        "cancel_task": {"task_id": {"type": "string"}},
        "list_task_artifacts": {"task_id": {"type": "string"}},
        "get_task_artifact": {"task_id": {"type": "string"}, "artifact_id": {"type": "string"}},
        "download_task_artifact": {"task_id": {"type": "string"}, "artifact_id": {"type": "string"}},
        "list_project_skills": {},
        "run_project_skill": {"agent_id": {"type": "string"}, "skill_name": {"type": "string"}, "instruction": {"type": "string"}},
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
    return [{"name": name, "description": f"AgentSlack project-scoped {name}",
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
        value = adapter.call(tool_name, dict(params.get("arguments") or {}))
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
