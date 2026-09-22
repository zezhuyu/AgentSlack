from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from agent_slack.mcp_server import AgentSlackMCP, serve_stdio
from agent_slack.server import run_server
from agent_slack.servers import AgentServerManager


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Agent Slack daemon")
    parser.add_argument("--project-root", default=None, help="Optional initial agent-system folder")
    parser.add_argument("--data-root", default=None, help="Writable Agent Slack application data folder")
    parser.add_argument("--app-root", default=None, help="Application/repository root used for project discovery")
    parser.add_argument("--mcp-stdio", action="store_true", help="Run the project-scoped MCP server over stdio")
    parser.add_argument(
        "--host",
        default=os.getenv("AGENT_SLACK_IP", os.getenv("AGENT_SLACK_HOST", "0.0.0.0")),
        help="Bind IP (default: AGENT_SLACK_IP or 0.0.0.0)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.getenv("AGENT_SLACK_PORT", "8899")),
        help="Bind port (default: AGENT_SLACK_PORT or 8899)",
    )
    args = parser.parse_args()
    data_root = Path(args.data_root).expanduser().resolve() if args.data_root else None
    app_root = Path(args.app_root).expanduser().resolve() if args.app_root else Path(
        getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)
    )
    if args.mcp_stdio:
        if data_root is None:
            parser.error("--data-root is required with --mcp-stdio")
        serve_stdio(AgentSlackMCP(AgentServerManager(app_root, data_root)))
        return
    run_server(
        project_root=Path(args.project_root).resolve() if args.project_root else None,
        data_root=data_root,
        host=args.host,
        port=args.port,
    )


if __name__ == "__main__":
    main()
