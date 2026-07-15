from __future__ import annotations

import argparse
from pathlib import Path

from agent_slack.server import run_server


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Agent Slack daemon")
    parser.add_argument("--project-root", default=None, help="Optional initial agent-system folder")
    parser.add_argument("--data-root", default=None, help="Writable Agent Slack application data folder")
    parser.add_argument("--host", default="127.0.0.1", help="Bind host")
    parser.add_argument("--port", type=int, default=8899, help="Bind port")
    args = parser.parse_args()
    run_server(
        project_root=Path(args.project_root).resolve() if args.project_root else None,
        data_root=Path(args.data_root).expanduser().resolve() if args.data_root else None,
        host=args.host,
        port=args.port,
    )


if __name__ == "__main__":
    main()
