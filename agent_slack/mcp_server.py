"""Small MCP-compatible stdio adapter for AgentSlack.

The adapter intentionally exposes project-scoped read/context tools and durable
task submission only. It never exposes a shell, arbitrary file access, or writes.
"""
from __future__ import annotations

import json
import sys
import argparse
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
    }
    required = {
        "get_agent_status": [], "search_memory": ["query"],
        "append_project_decision": ["content"], "append_project_insight": ["content"],
        "update_project_summary": ["section", "content"], "search_project_files": ["query"],
        "read_project_file": ["path"], "get_task": ["task_id"],
        "create_task": ["agent_id", "instruction"], "cancel_task": ["task_id"],
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
        result = {"protocolVersion": protocol_version, "capabilities": {"tools": {}},
                  "serverInfo": {"name": "agent-slack", "version": "1"}}
    elif method == "server/discover":
        # MCP 2026-07-28 uses a stateless discovery exchange instead of the
        # legacy initialize handshake. OpenAI Secure MCP Tunnel/ChatGPT sends
        # this probe before tools/list, so return the complete DiscoverResult
        # shape required by that protocol revision.
        result = {
            "resultType": "complete",
            "supportedVersions": ["2026-07-28", "2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05"],
            "capabilities": {"tools": {}},
            "_meta": {"io.modelcontextprotocol/serverInfo": {"name": "agent-slack", "version": "1"}},
            "instructions": "AgentSlack provides project-scoped files, memory, status, and durable tasks. Every tool requires workspace_key.",
            "ttlMs": 0,
            "cacheScope": "private",
        }
    elif method == "tools/list":
        result = {"tools": _tool_definitions(), "ttlMs": 0, "cacheScope": "private"}
    elif method == "tools/call":
        params = request.get("params") or {}
        result = {"content": [{"type": "text", "text": json.dumps(
            adapter.call(str(params.get("name") or ""), dict(params.get("arguments") or {})),
            ensure_ascii=False)}]}
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
