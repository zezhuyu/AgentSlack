from __future__ import annotations

import json
import shutil
import threading
import uuid
from pathlib import Path
from typing import Any

from .app import AgentSlackApp
from .storage import utc_now


class AgentServerManager:
    SCHEMA_VERSION = 1

    def __init__(self, app_root: Path, data_root: Path, initial_project_root: Path | None = None):
        self.app_root = app_root.resolve()
        self.data_root = data_root.expanduser().resolve()
        self.static_root = self.app_root / "static"
        self.registry_file = self.data_root / "servers.json"
        self._lock = threading.RLock()
        self._apps: dict[str, AgentSlackApp] = {}
        self.data_root.mkdir(parents=True, exist_ok=True)
        self._registry = self._load_registry()
        if initial_project_root is not None:
            self._ensure_initial_server(initial_project_root)

    @property
    def active_server_id(self) -> str | None:
        value = self._registry.get("active_server_id")
        return str(value) if value else None

    def list_servers(self) -> list[dict[str, Any]]:
        active_id = self.active_server_id
        return [
            {
                "server_id": item["server_id"],
                "name": item["name"],
                "project_root": item["project_root"],
                "project_name": Path(item["project_root"]).name or item["name"],
                "available": Path(item["project_root"]).is_dir(),
                "active": item["server_id"] == active_id,
                "logo_url": (
                    f"/api/servers/{item['server_id']}/logo" if item.get("logo_file") else None
                ),
                "logo_revision": item.get("logo_revision"),
            }
            for item in self._registry["servers"]
        ]

    def add_server(
        self,
        project_root: Path,
        name: str | None = None,
        logo_path: Path | None = None,
    ) -> dict[str, Any]:
        root = project_root.expanduser().resolve()
        if not root.is_dir():
            raise ValueError(f"Agent system folder does not exist: {root}")
        with self._lock:
            existing = next(
                (item for item in self._registry["servers"] if Path(item["project_root"]) == root),
                None,
            )
            if existing is None:
                server_id = uuid.uuid4().hex[:12]
                existing = {
                    "server_id": server_id,
                    "name": (name or root.name or "Agent System").strip(),
                    "project_root": str(root),
                    "storage_key": server_id,
                    "created_at": utc_now(),
                }
                self._registry["servers"].append(existing)
            elif name and name.strip():
                existing["name"] = name.strip()
            self._registry["active_server_id"] = existing["server_id"]
            if logo_path is not None:
                self._store_logo(existing, logo_path)
            self._save_registry()
            self._app_for(existing["server_id"], refresh=True)
            return next(item for item in self.list_servers() if item["server_id"] == existing["server_id"])

    def update_server(
        self,
        server_id: str,
        name: str | None = None,
        logo_path: Path | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            item = self._find(server_id)
            if item is None:
                raise KeyError(f"Server not found: {server_id}")
            if name is not None and name.strip():
                item["name"] = name.strip()
            if logo_path is not None:
                self._store_logo(item, logo_path)
            self._save_registry()
            return next(server for server in self.list_servers() if server["server_id"] == server_id)

    def logo_path(self, server_id: str) -> Path | None:
        item = self._find(server_id)
        if item is None:
            raise KeyError(f"Server not found: {server_id}")
        logo_file = item.get("logo_file")
        if not logo_file:
            return None
        path = (self.data_root / logo_file).resolve()
        if not path.is_relative_to(self.data_root) or not path.is_file():
            return None
        return path

    def activate(self, server_id: str) -> dict[str, Any]:
        with self._lock:
            item = self._find(server_id)
            if item is None:
                raise KeyError(f"Server not found: {server_id}")
            if not Path(item["project_root"]).is_dir():
                raise ValueError(f"Agent system folder is unavailable: {item['project_root']}")
            self._registry["active_server_id"] = server_id
            self._save_registry()
            self._app_for(server_id)
            return next(server for server in self.list_servers() if server["server_id"] == server_id)

    def active_app(self) -> AgentSlackApp:
        server_id = self.active_server_id
        if server_id is None:
            raise LookupError("No agent server is configured")
        return self._app_for(server_id)

    def app_for(self, server_id: str) -> AgentSlackApp:
        return self._app_for(server_id)

    def active_summary(self) -> dict[str, Any] | None:
        server_id = self.active_server_id
        if server_id is None:
            return None
        item = self._find(server_id)
        if item is None:
            return None
        return next((server for server in self.list_servers() if server["server_id"] == server_id), None)

    def _ensure_initial_server(self, project_root: Path) -> None:
        root = project_root.expanduser().resolve()
        if not root.is_dir():
            return
        existing = next(
            (item for item in self._registry["servers"] if Path(item["project_root"]) == root),
            None,
        )
        if existing is None:
            server_id = uuid.uuid4().hex[:12]
            storage_key = "legacy" if self._has_legacy_data() and not self._registry["servers"] else server_id
            existing = {
                "server_id": server_id,
                "name": root.name or "Agent System",
                "project_root": str(root),
                "storage_key": storage_key,
                "created_at": utc_now(),
            }
            self._registry["servers"].append(existing)
        if self.active_server_id is None:
            self._registry["active_server_id"] = existing["server_id"]
        self._save_registry()

    def _app_for(self, server_id: str, refresh: bool = False) -> AgentSlackApp:
        item = self._find(server_id)
        if item is None:
            raise KeyError(f"Server not found: {server_id}")
        root = Path(item["project_root"])
        if not root.is_dir():
            raise ValueError(f"Agent system folder is unavailable: {root}")
        if server_id not in self._apps:
            storage_key = item.get("storage_key") or server_id
            server_data_root = self.data_root if storage_key == "legacy" else self.data_root / "servers" / storage_key
            self._apps[server_id] = AgentSlackApp(
                project_root=root,
                app_root=self.app_root,
                data_root=server_data_root,
            )
        elif refresh:
            self._apps[server_id].architecture = self._apps[server_id].architecture.load(root)
            self._apps[server_id].refresh_agents()
        return self._apps[server_id]

    def _find(self, server_id: str) -> dict[str, Any] | None:
        return next((item for item in self._registry["servers"] if item["server_id"] == server_id), None)

    def _has_legacy_data(self) -> bool:
        return (self.data_root / "agents.json").exists() or (self.data_root / "chats").is_dir()

    def _store_logo(self, item: dict[str, Any], source_path: Path) -> None:
        source = source_path.expanduser().resolve()
        if not source.is_file():
            raise ValueError(f"Server logo does not exist: {source}")
        suffix = source.suffix.casefold()
        if suffix not in {".png", ".jpg", ".jpeg", ".webp", ".gif"}:
            raise ValueError("Server logo must be PNG, JPEG, WebP, or GIF")
        asset_dir = self.data_root / "server-assets" / item["server_id"]
        asset_dir.mkdir(parents=True, exist_ok=True)
        target = asset_dir / f"logo{suffix}"
        for old_logo in asset_dir.glob("logo.*"):
            if old_logo != target:
                old_logo.unlink()
        shutil.copy2(source, target)
        item["logo_file"] = target.relative_to(self.data_root).as_posix()
        item["logo_revision"] = utc_now()

    def _load_registry(self) -> dict[str, Any]:
        default = {"schema_version": self.SCHEMA_VERSION, "active_server_id": None, "servers": []}
        if not self.registry_file.exists():
            return default
        try:
            payload = json.loads(self.registry_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return default
        servers = [item for item in payload.get("servers") or [] if isinstance(item, dict)]
        return {
            "schema_version": self.SCHEMA_VERSION,
            "active_server_id": payload.get("active_server_id"),
            "servers": servers,
        }

    def _save_registry(self) -> None:
        self.registry_file.write_text(json.dumps(self._registry, ensure_ascii=False, indent=2), encoding="utf-8")
