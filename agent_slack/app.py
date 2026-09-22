from __future__ import annotations

import json
import mimetypes
import os
import re
import shutil
from threading import Thread
from collections import Counter
from collections.abc import Iterator
from copy import deepcopy
from pathlib import Path
from threading import RLock
from typing import Any
from uuid import uuid4

from .architecture import AgentSystemArchitecture
from .chat.engine import ChatEngine
from .chat.openai_provider import OpenAICompatibleChatProvider
from .chat.provider import CliChatProvider
from .chat.session import ChatSessionStore
from .discovery import AgentDiscovery
from .interactions import extract_choice_interaction
from .knowledge import ProjectKnowledge
from .memory_search import ProjectMemorySearch
from .project_skills import list_project_skills, resolve_project_skill
from .shared_memory import ProjectSharedMemory
from .orchestrator import CliOrchestrator
from .retrieval.manager import RetrievalManager
from .retrieval.paths import is_shareable as is_shareable_path
from .retrieval.paths import resolve_within as resolve_within_path
from .storage import AgentSlackStorage, utc_now
from .voice import LocalVoiceBackend


# Build/dependency/cache noise an agent's working directory routinely accumulates
# (venvs, package caches, tool scratch dirs) - never worth attaching to chat even
# though is_shareable() (a *security* filter, not a noise filter) lets them through.
NOISE_DIRECTORY_NAMES = {
    "venv", ".venv", "env", ".env-dir", "__pycache__", "node_modules", ".cache",
    "site-packages", "dist", "build", ".tox", ".mypy_cache", ".pytest_cache",
    ".ruff_cache", "egg-info", ".eggs", ".ipynb_checkpoints",
}
NOISE_FILE_NAMES = {"cachedir.tag", "pyvenv.cfg", ".ds_store"}

# Content types a chat attachment card should render inline (audio player, image,
# 3D viewer, ...) because they're a primary deliverable, not incidental reference
# material (docs/manifests/config an agent wrote to explain its own output).
PRIMARY_CONTENT_TYPE_PREFIXES = ("image/", "audio/", "video/", "model/")
PRIMARY_CONTENT_TYPES = {"application/pdf"}

# mimetypes.guess_type() doesn't know these on many platforms (.glb/.gltf are never
# registered in the stdlib db at all) - without this fallback, generated 3D models
# and some audio formats get stored as "application/octet-stream" and never preview.
EXTENSION_CONTENT_TYPES = {
    ".glb": "model/gltf-binary", ".gltf": "model/gltf+json",
    ".obj": "model/obj", ".blend": "model/blend", ".fbx": "model/vnd.autodesk.fbx",
    ".stl": "model/stl", ".usdz": "model/vnd.usdz+zip",
    ".md": "text/markdown", ".markdown": "text/markdown", ".json": "application/json",
    ".mp3": "audio/mpeg", ".wav": "audio/wav", ".m4a": "audio/mp4", ".ogg": "audio/ogg",
    ".mid": "audio/midi", ".midi": "audio/midi",
    ".webm": "video/webm", ".mov": "video/quicktime",
}


def _is_noise_path(path: Path) -> bool:
    if any(part.lower() in NOISE_DIRECTORY_NAMES for part in path.parts):
        return True
    return path.name.casefold() in NOISE_FILE_NAMES


def _guess_content_type(path: Path) -> str:
    return EXTENSION_CONTENT_TYPES.get(path.suffix.lower()) or mimetypes.guess_type(str(path))[0] or "application/octet-stream"


def _attachment_role(content_type: str) -> str:
    """"primary" gets a full inline preview; "reference" gets a link-only chip."""
    if content_type.startswith(PRIMARY_CONTENT_TYPE_PREFIXES) or content_type in PRIMARY_CONTENT_TYPES:
        return "primary"
    return "reference"


class AgentSlackApp:
    MAX_UPLOAD_BYTES = 50 * 1024 * 1024
    MAX_ATTACHMENTS_PER_MESSAGE = 10

    def __init__(
        self,
        project_root: Path,
        app_root: Path,
        data_root: Path | None = None,
        runner_override: str | None = None,
        model_override: str | None = None,
    ):
        self.project_root = project_root
        self.app_root = app_root
        self.data_root = data_root or self.app_root / "data"
        self.static_root = self.app_root / "static"
        self.discovery = AgentDiscovery(project_root)
        self.storage = AgentSlackStorage(self.data_root)
        self.workspace_name = self.project_root.name
        self.runner_override = str(runner_override or "").strip().casefold() or None
        self.model_override = str(model_override or "").strip() or None
        self._run_state_lock = RLock()
        self._chat_interaction_lock = RLock()
        self._active_run_states: dict[str, dict[str, Any]] = {}
        self.voice = LocalVoiceBackend()
        self.reload_host_configuration()
        self.retrieval = RetrievalManager(self.project_root, self.data_root / "search" / "index.sqlite")
        self.chat_sessions = ChatSessionStore(self.data_root / "chat_sessions")
        self.chat_provider = self._build_chat_provider()
        self.chat_engine = ChatEngine(self.chat_sessions, self.retrieval, self.chat_provider, self.get_upload_path)
        self.bootstrap()

    @property
    def workspace_key(self) -> str:
        return self.architecture.workspace_key or self.project_root.name

    def project_knowledge(self) -> ProjectKnowledge:
        return ProjectKnowledge(self.project_root, self.architecture.knowledge_roots)

    def list_project_files(self) -> list[dict[str, Any]]:
        return self.project_knowledge().list_project_files()

    def search_project_files(self, query: str) -> list[dict[str, Any]]:
        return self.project_knowledge().search_project_files(query)

    def read_project_file(self, path: str) -> dict[str, Any]:
        return self.project_knowledge().read_project_file(path)

    def search_memory(self, query: str, sources: list[str] | None = None, limit: int = 10) -> list[dict[str, Any]]:
        shared = ProjectSharedMemory.for_app(self).search(self.workspace_key, query, limit=limit)
        local = ProjectMemorySearch(self.project_root, self.data_root).search_memory(query)
        results = shared + local
        if sources:
            results = [item for item in results if item.get("source") in set(sources)]
        return sorted(results, key=lambda item: (-float(item.get("score", 0)), item.get("path", "")))[:max(1, limit)]

    def get_project_memory(self) -> dict[str, Any]:
        return ProjectSharedMemory.for_app(self).get_project_memory(self.workspace_key)

    def append_project_decision(self, content: str, rationale: str | None = None, source: str | None = None, **metadata: Any) -> dict[str, Any]:
        return ProjectSharedMemory.for_app(self).append_decision(self.workspace_key, content, rationale, source, **metadata)

    def append_project_insight(self, content: str, insight_type: str = "general", source: str | None = None, **metadata: Any) -> dict[str, Any]:
        return ProjectSharedMemory.for_app(self).append_insight(self.workspace_key, content, insight_type, source, **metadata)

    def update_project_summary(self, section: str, content: str, source: str | None = None) -> dict[str, Any]:
        return ProjectSharedMemory.for_app(self).update_project_summary(self.workspace_key, section, content, source)

    def list_tasks(self) -> list[dict[str, Any]]:
        return self.storage.list_tasks()

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        return self.storage.get_task(task_id)

    def create_task(self, agent_id: str, instruction: str, source: str = "chatgpt") -> dict[str, Any]:
        agent = self.get_agent(agent_id)
        if agent is None:
            raise ValueError(f"Agent not found: {agent_id}")
        task = self.storage.create_task({
            "workspace_key": self.workspace_key,
            "agent_id": agent_id,
            "instruction": str(instruction).strip(),
            "status": "queued",
            "source": source,
            "run_id": None,
            "result": None,
            "artifacts": [],
            "completed_at": None,
        })
        Thread(target=self._run_project_task, args=(task["task_id"],), daemon=True).start()
        return task

    def _run_project_task(self, task_id: str) -> None:
        task = self.storage.get_task(task_id)
        if not task:
            return
        try:
            chat = self.create_chat(f"Task {task_id}", [task["agent_id"]], kind="task")
            self.storage.append_message(chat["chat_id"], {
                "author_type": "user", "author_id": "external", "author_label": task["source"],
                "body": task["instruction"], "metadata": {"task_id": task_id},
            })
            self.storage.update_task(task_id, status="running", chat_id=chat["chat_id"])
            result = ""
            run_id = None
            run_failed = False
            for event in self.stream_run(chat["chat_id"], mode="respond", objective=task["instruction"],
                                         agent_ids=[task["agent_id"]], lead_agent_id=task["agent_id"]):
                if event.get("type") == "run_started":
                    run_id = event.get("run_id")
                    self.storage.update_task(task_id, run_id=run_id)
                if event.get("type") in {"agent_completed", "agent_failed"}:
                    result = str(event.get("message") or event.get("body") or result)
                if event.get("type") == "agent_failed":
                    run_failed = True
                if event.get("type") in {"run_cancelled", "agent_cancelled"}:
                    self.storage.update_task(task_id, status="cancelled", completed_at=utc_now(), result=result)
                    return
            saved_chat = self.storage.get_chat(chat["chat_id"]) or {}
            artifacts = []
            for message in saved_chat.get("messages", []):
                if (message.get("metadata") or {}).get("run_id") == run_id:
                    artifacts.extend((message.get("metadata") or {}).get("attachments") or [])
            final_status = "failed" if run_failed else "completed"
            self.storage.update_task(task_id, status=final_status, completed_at=utc_now(), result=result, artifacts=artifacts)
            if result.strip() and not run_failed:
                self.append_project_insight(
                    f"{task_id} completed: {result.strip()[:1200]}",
                    insight_type="implementation", source=self.orchestrator.backend, task_id=task_id,
                )
        except Exception as exc:
            self.storage.update_task(task_id, status="failed", completed_at=utc_now(), result=str(exc))

    def cancel_task_record(self, task_id: str) -> dict[str, Any]:
        task = self.storage.get_task(task_id)
        if task is None:
            raise KeyError(f"Task not found: {task_id}")
        if task.get("run_id"):
            self.cancel_run(str(task["run_id"]))
        return self.storage.update_task(task_id, status="cancelled", completed_at=utc_now())

    def list_task_artifacts(self, task_id: str) -> list[dict[str, Any]]:
        task = self.storage.get_task(task_id)
        if task is None:
            raise KeyError(f"Task not found: {task_id}")
        artifacts = []
        for item in task.get("artifacts") or []:
            artifact_id = str(item.get("attachment_id") or "")
            if not self.get_artifact_path(artifact_id):
                continue
            artifacts.append({
                "artifact_id": artifact_id,
                "filename": item.get("filename"),
                "content_type": item.get("content_type", "application/octet-stream"),
                "size": item.get("size", 0),
                "role": item.get("role", "primary"),
                "relative_path": item.get("relative_path"),
                "resource_uri": f"agent-slack://{self.workspace_key}/tasks/{task_id}/artifacts/{artifact_id}",
                # Compatibility for clients that still have the original
                # list_task_artifacts tool cached and have not refreshed the
                # newer download_task_artifact descriptor yet.
                "file_uri": {
                    "download_url": f"agent-slack://{self.workspace_key}/tasks/{task_id}/artifacts/{artifact_id}",
                    "file_id": artifact_id,
                    "mime_type": item.get("content_type", "application/octet-stream"),
                    "file_name": item.get("filename"),
                },
            })
        return artifacts

    def get_task_artifact(self, task_id: str, artifact_id: str) -> tuple[Path, dict[str, Any]]:
        allowed = {item["artifact_id"] for item in self.list_task_artifacts(task_id)}
        if artifact_id not in allowed:
            raise KeyError("Artifact is not attached to this project task")
        artifact = self.get_artifact_path(artifact_id)
        if artifact is None:
            raise FileNotFoundError("Artifact not found")
        return artifact

    def list_project_skills(self) -> list[dict[str, Any]]:
        return list_project_skills(self.project_root)

    def run_project_skill(self, skill_name: str, instruction: str, agent_id: str) -> dict[str, Any]:
        skill = resolve_project_skill(self.project_root, skill_name)
        task_instruction = (
            f"Use the project skill {skill['skill_name']} from {skill['path']}. "
            "Read its complete SKILL.md before acting and follow any specified Python scripts "
            "through the existing project runner. Do not use unrelated skills or project roots.\n\n"
            f"User request:\n{str(instruction).strip()}"
        )
        task = self.create_task(agent_id, task_instruction, source="chatgpt")
        return self.storage.update_task(task["task_id"], skill_name=skill["skill_name"])

    def _build_chat_provider(self):
        """Prefer a directly-configured OpenAI-compatible endpoint (no CLI bootstrap
        per turn, so it's the fastest option) and fall back to the local Claude/Codex
        CLI otherwise. Either way Chat mode only ever gets a system prompt + plain
        text - never a shell or filesystem tool."""
        base_url = os.environ.get("AGENT_SLACK_CHAT_LLM_BASE_URL", "").strip()
        if base_url:
            external = OpenAICompatibleChatProvider(
                base_url=base_url,
                api_key=os.environ.get("AGENT_SLACK_CHAT_LLM_API_KEY", "").strip() or None,
                model=os.environ.get("AGENT_SLACK_CHAT_LLM_MODEL", "").strip(),
            )
            if external.available:
                return external
        return CliChatProvider(
            self.orchestrator.backend, self.orchestrator.executable, self.project_root, self.orchestrator.model,
        )

    def reload_host_configuration(self) -> None:
        self.architecture = AgentSystemArchitecture.load(self.project_root)
        if hasattr(self, "orchestrator") and self.orchestrator.has_active_runs:
            return
        discovered = self.discovery.discover()
        project_fallback = len(discovered) == 1 and discovered[0].kind == "project"
        runner = self.runner_override or (
            "claude"
            if project_fallback and self.architecture.runner == "auto"
            else self.architecture.runner
        )
        self.orchestrator = CliOrchestrator(
            self.workspace_name,
            self.project_root,
            backend_preference=runner,
            model=self.model_override if project_fallback else None,
        )
        if hasattr(self, "chat_engine"):
            self.chat_provider = self._build_chat_provider()
            self.chat_engine.provider = self.chat_provider

    def configure_runtime(self, runner: str | None, model: str | None) -> None:
        self.runner_override = str(runner or "").strip().casefold() or None
        self.model_override = str(model or "").strip() or None
        self.reload_host_configuration()

    def bootstrap(self) -> None:
        if not self.storage.load_agents():
            self.refresh_agents()

    def refresh_agents(self) -> list[dict[str, Any]]:
        agents = [item.to_dict() for item in self.discovery.discover()]
        self.storage.save_agents(agents)
        for agent in agents:
            self.update_agent_memory(agent["agent_id"])
        return agents

    def list_agents(self) -> list[dict[str, Any]]:
        return self.storage.load_agents()

    def get_agent(self, agent_id: str) -> dict[str, Any] | None:
        return next((agent for agent in self.list_agents() if agent["agent_id"] == agent_id), None)

    def architecture_summary(self) -> dict[str, Any]:
        return self.architecture.summary(self.project_root)

    def voice_capabilities(self) -> dict[str, Any]:
        """Capabilities advertised to native clients and the local web panel."""
        return {
            "enabled": True,
            "transport": "shared_chat_run_stream",
            **self.voice.capabilities(),
            "interruption": {"stop_audio": True, "cancel_agent_on_barge_in": False},
            "files": {"upload": True, "preview": True, "max_bytes": self.MAX_UPLOAD_BYTES},
        }

    def create_voice_session(self, source_chat_id: str) -> dict[str, Any]:
        source = self.storage.get_chat(source_chat_id)
        if source is None:
            raise KeyError(f"Chat not found: {source_chat_id}")
        title = f"Voice · {source.get('title') or 'Conversation'}"
        return self.create_chat(title, list(source.get("member_ids", [])), kind="voice")

    def store_speech(self, content: bytes) -> str:
        speech_id = uuid4().hex
        directory = (self.data_root / "speech" / speech_id).resolve()
        directory.mkdir(parents=True, exist_ok=False)
        (directory / "speech.wav").write_bytes(content)
        return speech_id

    def speech_path(self, speech_id: str) -> Path | None:
        if not re.fullmatch(r"[0-9a-f]{32}", str(speech_id or "")):
            return None
        path = (self.data_root / "speech" / speech_id / "speech.wav").resolve()
        root = (self.data_root / "speech").resolve()
        return path if path.is_file() and path.is_relative_to(root) else None

    def stream_voice_turn(self, chat_id: str, audio: bytes, suffix: str, request_id: str) -> Iterator[dict[str, Any]]:
        text = self.voice.transcribe(audio, suffix)
        yield {"type": "voice_transcript", "text": text}
        if not text:
            yield {"type": "voice_turn_failed", "message": "No speech was recognized"}
            return
        _chat, is_new = self.submit_voice_utterance(chat_id, text, request_id)
        if not is_new:
            yield {"type": "voice_turn_duplicate", "request_id": request_id}
            return
        events = self.stream_run(chat_id=chat_id, mode="respond")
        latest_reply = ""
        for event in events:
            if event.get("type") == "agent_completed":
                latest_reply = str(event.get("message") or event.get("body") or latest_reply)
            yield event
        if not latest_reply:
            chat = self.storage.get_chat(chat_id) or {}
            latest_reply = next((str(message.get("body") or "") for message in reversed(chat.get("messages", [])) if message.get("author_type") == "agent"), "")
        if latest_reply:
            speech_id = self.store_speech(self.voice.synthesize(latest_reply))
            yield {
                "type": "speech_ready",
                "speech_id": speech_id,
                "url": f"/api/v1/voice/speech/{speech_id}",
                "text": latest_reply,
            }

    def list_chats(self) -> list[dict[str, Any]]:
        chats = self.storage.list_chats()
        agents = {agent["agent_id"]: agent for agent in self.list_agents()}
        summaries = []
        for chat in chats:
            messages = [
                message
                for message in chat.get("messages", [])
                if message.get("author_type") != "system"
            ]
            last = messages[-1] if messages else None
            summaries.append(
                {
                    "chat_id": chat["chat_id"],
                    "title": chat["title"],
                    "kind": chat["kind"],
                    "member_ids": chat.get("member_ids", []),
                    "member_titles": [agents.get(member_id, {}).get("title", member_id) for member_id in chat.get("member_ids", [])],
                    "updated_at": chat.get("updated_at"),
                    "last_message_preview": (last or {}).get("body", "")[:120],
                    "message_count": len(messages),
                }
            )
        return summaries

    def get_chat(self, chat_id: str) -> dict[str, Any] | None:
        return self.storage.get_chat(chat_id)

    def list_active_runs(self, chat_id: str | None = None) -> list[dict[str, Any]]:
        """Return reconnect-safe UI state without exposing runner internals."""
        with self._run_state_lock:
            runs = [
                deepcopy(run)
                for run in self._active_run_states.values()
                if chat_id is None or run["chat_id"] == chat_id
            ]
        for run in runs:
            run["tasks"] = list(run["tasks"].values())
        return runs

    def _begin_run_state(
        self,
        run_id: str,
        chat_id: str,
        mode: str,
        lead_agent_id: str,
    ) -> None:
        with self._run_state_lock:
            self._active_run_states[run_id] = {
                "run_id": run_id,
                "chat_id": chat_id,
                "mode": mode,
                "lead_agent_id": lead_agent_id,
                "status": "running",
                "started_at": utc_now(),
                "tasks": {},
            }

    def _update_run_task(
        self,
        run_id: str,
        task_id: str,
        agent_id: str,
        agent_label: str,
        *,
        status: str | None = None,
        text_delta: str = "",
        text: str | None = None,
    ) -> None:
        with self._run_state_lock:
            run = self._active_run_states.get(run_id)
            if not run:
                return
            task = run["tasks"].setdefault(
                task_id,
                {
                    "task_id": task_id,
                    "agent_id": agent_id,
                    "agent_label": agent_label,
                    "status": "running",
                    "text": "",
                },
            )
            if agent_label:
                task["agent_label"] = agent_label
            if status:
                task["status"] = status
            if text is not None:
                task["text"] = text
            elif text_delta:
                task["text"] += text_delta

    def _run_events(self, run_id: str, events: Iterator[dict[str, Any]]) -> Iterator[dict[str, Any]]:
        try:
            yield from events
        finally:
            with self._run_state_lock:
                self._active_run_states.pop(run_id, None)

    def _chat_has_active_run(self, chat_id: str) -> bool:
        with self._run_state_lock:
            return any(run["chat_id"] == chat_id for run in self._active_run_states.values())

    def delete_chat(self, chat_id: str) -> dict[str, Any]:
        chat = self.storage.get_chat(chat_id)
        if chat is None:
            raise KeyError(f"Chat not found: {chat_id}")
        if self._chat_has_active_run(chat_id):
            raise ValueError(f"Cannot delete chat {chat_id} while a run is still active")
        agent_ids = set(chat.get("member_ids", []))
        agent_ids.update(
            message.get("author_id")
            for message in chat.get("messages", [])
            if message.get("author_type") == "agent"
        )
        self.storage.delete_chat(chat_id)
        for agent_id in filter(None, agent_ids):
            if self.get_agent(str(agent_id)):
                try:
                    self.update_agent_memory(str(agent_id))
                except Exception:
                    pass
        return {"deleted": True, "chat_id": chat_id}

    def create_chat(self, title: str, member_ids: list[str], kind: str = "group") -> dict[str, Any]:
        chat = self.storage.create_chat(title=title, member_ids=member_ids, kind=kind)
        self._append_system_message(chat["chat_id"], f"Chat created for: {', '.join(member_ids)}")
        return self.storage.get_chat(chat["chat_id"]) or chat

    def store_upload(self, filename: str, content_type: str, content: bytes) -> dict[str, Any]:
        if not content:
            raise ValueError("Upload is empty")
        if len(content) > self.MAX_UPLOAD_BYTES:
            raise ValueError("Upload exceeds the 50 MB limit")
        safe_name = self._safe_upload_filename(filename)
        attachment_id = uuid4().hex
        upload_dir = (self.data_root / "uploads" / attachment_id).resolve()
        upload_dir.mkdir(parents=True, exist_ok=False)
        file_path = upload_dir / safe_name
        file_path.write_bytes(content)
        metadata = {
            "attachment_id": attachment_id,
            "filename": safe_name,
            "content_type": str(content_type or "application/octet-stream").split(";", 1)[0].strip()
            or "application/octet-stream",
            "size": len(content),
        }
        (upload_dir / "metadata.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return {**metadata, "path": str(file_path.resolve())}

    def get_upload_path(self, attachment_id: str) -> tuple[Path, dict[str, Any]] | None:
        """Resolve an attachment through its opaque ID, never a client path."""
        if not re.fullmatch(r"[0-9a-f]{32}", str(attachment_id or "")):
            return None
        uploads_root = (self.data_root / "uploads").resolve()
        upload_dir = (uploads_root / attachment_id).resolve()
        metadata_path = upload_dir / "metadata.json"
        if not upload_dir.is_relative_to(uploads_root) or not metadata_path.is_file():
            return None
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            filename = self._safe_upload_filename(str(metadata["filename"]))
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            return None
        file_path = (upload_dir / filename).resolve()
        if not file_path.is_relative_to(upload_dir) or not file_path.is_file():
            return None
        return file_path, {**metadata, "filename": filename}

    def store_created_artifact(self, source: Path) -> dict[str, Any] | None:
        if not self._is_shareable_artifact(source) or _is_noise_path(source):
            return None
        if source.stat().st_size > self.MAX_UPLOAD_BYTES:
            return None
        artifact_id = uuid4().hex
        artifact_dir = (self.data_root / "artifacts" / artifact_id).resolve()
        artifact_dir.mkdir(parents=True, exist_ok=False)
        target = artifact_dir / self._safe_upload_filename(source.name)
        shutil.copyfile(source, target)
        content_type = _guess_content_type(source)
        metadata = {
            "attachment_id": artifact_id,
            "filename": target.name,
            "content_type": content_type,
            "role": _attachment_role(content_type),
            "size": target.stat().st_size,
            "url": f"/api/artifacts/{artifact_id}",
        }
        try:
            metadata["relative_path"] = source.resolve().relative_to(self.project_root.resolve()).as_posix()
        except ValueError:
            return None
        (artifact_dir / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
        return metadata

    @staticmethod
    def _is_shareable_artifact(source: Path) -> bool:
        return is_shareable_path(source)

    def get_artifact_path(self, artifact_id: str) -> tuple[Path, dict[str, Any]] | None:
        if not re.fullmatch(r"[0-9a-f]{32}", str(artifact_id or "")):
            return None
        root = (self.data_root / "artifacts").resolve()
        directory = (root / artifact_id).resolve()
        metadata_path = directory / "metadata.json"
        if not directory.is_relative_to(root) or not metadata_path.is_file():
            return None
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            filename = self._safe_upload_filename(str(metadata["filename"]))
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            return None
        path = (directory / filename).resolve()
        if not path.is_file() or not path.is_relative_to(directory):
            return None
        # Older macOS MIME tables misclassified OBJ as application/x-tgif and
        # left BLEND as octet-stream. Normalize only these known legacy generic
        # labels so previously-created task artifacts remain downloadable with
        # useful type information after an app upgrade.
        content_type = str(metadata.get("content_type") or "")
        known_type = EXTENSION_CONTENT_TYPES.get(path.suffix.lower())
        if known_type and content_type in {"application/x-tgif", "application/octet-stream"}:
            metadata["content_type"] = known_type
        return path, {**metadata, "filename": filename}

    def resolve_workspace_file(self, raw_path: str) -> Path | None:
        """Resolve a file an agent cited by path, scoped to the active project.

        Agent replies sometimes cite source files as plain filesystem paths (absolute
        or relative to the project root, optionally with a trailing ``:line`` locator).
        This lets the chat preview those without exposing anything outside the
        workspace the agent is actually running in.
        """
        return resolve_within_path(self.project_root, raw_path)

    def resolve_uploads(self, attachment_ids: list[str] | None) -> list[dict[str, Any]]:
        unique_ids = list(dict.fromkeys(str(value).strip() for value in attachment_ids or [] if str(value).strip()))
        if len(unique_ids) > self.MAX_ATTACHMENTS_PER_MESSAGE:
            raise ValueError(f"Messages support at most {self.MAX_ATTACHMENTS_PER_MESSAGE} attachments")
        attachments = []
        uploads_root = (self.data_root / "uploads").resolve()
        for attachment_id in unique_ids:
            if not re.fullmatch(r"[0-9a-f]{32}", attachment_id):
                raise ValueError(f"Unknown attachment: {attachment_id}")
            upload_dir = (uploads_root / attachment_id).resolve()
            metadata_path = upload_dir / "metadata.json"
            if not upload_dir.is_relative_to(uploads_root) or not metadata_path.is_file():
                raise ValueError(f"Unknown attachment: {attachment_id}")
            try:
                metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
                filename = self._safe_upload_filename(str(metadata["filename"]))
                file_path = (upload_dir / filename).resolve()
            except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
                raise ValueError(f"Unknown attachment: {attachment_id}") from None
            if not file_path.is_relative_to(upload_dir) or not file_path.is_file():
                raise ValueError(f"Unknown attachment: {attachment_id}")
            attachments.append(
                {
                    "attachment_id": attachment_id,
                    "filename": filename,
                    "path": str(file_path),
                    "content_type": str(metadata.get("content_type") or "application/octet-stream"),
                    "size": file_path.stat().st_size,
                }
            )
        return attachments

    @staticmethod
    def _safe_upload_filename(filename: str) -> str:
        leaf = Path(str(filename or "").replace("\\", "/")).name.strip()
        leaf = re.sub(r"[\x00-\x1f\x7f]", "_", leaf)
        leaf = re.sub(r"[:/]", "_", leaf)
        if leaf in {"", ".", ".."}:
            leaf = "attachment"
        if len(leaf) > 180:
            suffix = Path(leaf).suffix[:20]
            leaf = f"{leaf[: max(1, 180 - len(suffix))]}{suffix}"
        return leaf

    def add_user_message(
        self,
        chat_id: str,
        body: str,
        author_label: str = "You",
        attachment_ids: list[str] | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        key = str(idempotency_key or "").strip()
        with self._chat_interaction_lock:
            if key:
                chat = self.storage.get_chat(chat_id)
                if chat is None:
                    raise KeyError(f"Chat not found: {chat_id}")
                if any((message.get("metadata") or {}).get("idempotency_key") == key for message in chat.get("messages", [])):
                    return chat
            attachments = self.resolve_uploads(attachment_ids)
            metadata = {"attachments": attachments} if attachments else {}
            if key:
                metadata["idempotency_key"] = key
            chat = self.storage.append_message(
                chat_id,
                {
                    "author_type": "user",
                    "author_id": "user",
                    "author_label": author_label,
                    "body": body.strip(),
                    "metadata": metadata,
                },
            )
        for member_id in chat.get("member_ids", []):
            self.update_agent_memory(member_id)
        return chat

    def submit_voice_utterance(
        self, chat_id: str, body: str, request_id: str = ""
    ) -> tuple[dict[str, Any], bool]:
        """Atomically append a committed utterance and report whether it is new."""
        with self._chat_interaction_lock:
            existing = self.find_message_by_idempotency(chat_id, request_id)
            if existing:
                return self.storage.get_chat(chat_id) or {}, False
            return self.add_user_message(chat_id, body, idempotency_key=request_id), True

    def find_message_by_idempotency(self, chat_id: str, key: str) -> dict[str, Any] | None:
        key = str(key or "").strip()
        if not key:
            return None
        chat = self.storage.get_chat(chat_id)
        if chat is None:
            raise KeyError(f"Chat not found: {chat_id}")
        return next(
            (message for message in chat.get("messages", [])
             if (message.get("metadata") or {}).get("idempotency_key") == key),
            None,
        )

    def answer_question(
        self,
        chat_id: str,
        question_id: str,
        option_ids: list[str],
        author_label: str = "You",
    ) -> dict[str, Any]:
        with self._chat_interaction_lock:
            return self._answer_question_locked(
                chat_id,
                question_id,
                option_ids,
                author_label,
            )

    def _answer_question_locked(
        self,
        chat_id: str,
        question_id: str,
        option_ids: list[str],
        author_label: str,
    ) -> dict[str, Any]:
        chat = self.storage.get_chat(chat_id)
        if chat is None:
            raise KeyError(f"Chat not found: {chat_id}")
        interaction = None
        for message in reversed(chat.get("messages", [])):
            candidate = (message.get("metadata") or {}).get("interaction")
            if candidate and candidate.get("question_id") == question_id:
                interaction = candidate
                break
        if interaction is None:
            raise KeyError(f"Question not found: {question_id}")
        if interaction.get("kind") != "choice":
            raise ValueError(f"Unsupported question kind: {interaction.get('kind')}")
        if interaction.get("status") != "pending":
            raise ValueError("This question has already been answered")

        selected_ids = list(
            dict.fromkeys(str(value).strip() for value in option_ids if str(value).strip())
        )
        if interaction.get("multiple"):
            if not selected_ids:
                raise ValueError("Select at least one option")
        elif len(selected_ids) != 1:
            raise ValueError("Select exactly one option")

        options = {
            str(option.get("id")): option
            for option in interaction.get("options", [])
            if isinstance(option, dict) and option.get("id")
        }
        unknown = [option_id for option_id in selected_ids if option_id not in options]
        if unknown:
            raise ValueError(f"Unknown option: {unknown[0]}")

        labels = [str(options[option_id].get("label") or option_id) for option_id in selected_ids]
        interaction["status"] = "answered"
        interaction["selected_option_ids"] = selected_ids
        interaction["answered_at"] = utc_now()
        self.storage.save_chat(chat)
        prompt = str(interaction.get("prompt") or "the agent question")
        body = f'Response to "{prompt}": {", ".join(labels)}'
        updated = self.storage.append_message(
            chat_id,
            {
                "author_type": "user",
                "author_id": "user",
                "author_label": author_label,
                "body": body,
                "metadata": {
                    "interaction_response": {
                        "question_id": question_id,
                        "option_ids": selected_ids,
                        "option_labels": labels,
                    }
                },
            },
        )
        for member_id in updated.get("member_ids", []):
            self.update_agent_memory(member_id)
        return updated

    def run_agents(self, chat_id: str, agent_ids: list[str] | None = None, objective: str | None = None) -> dict[str, Any]:
        chat = self.storage.get_chat(chat_id)
        if chat is None:
            raise KeyError(f"Chat not found: {chat_id}")

        selected = agent_ids or chat.get("member_ids", [])
        lead_agent_id = next(iter(selected), None)
        if not lead_agent_id:
            raise ValueError("A lead agent is required to run the connected agent system")
        list(self.stream_run(
            chat_id,
            mode="respond",
            objective=objective,
            agent_ids=list(selected),
            lead_agent_id=lead_agent_id,
        ))
        return self.storage.get_chat(chat_id) or chat

    def cancel_run(self, run_id: str) -> dict[str, Any]:
        result = self.orchestrator.cancel_run(run_id)
        if result.get("accepted"):
            with self._run_state_lock:
                run = self._active_run_states.get(run_id)
                if run:
                    run["status"] = "stopping"
                    for task in run["tasks"].values():
                        if task["status"] == "running":
                            task["status"] = "stopping"
        return result

    def cancel_task(self, run_id: str, task_id: str) -> dict[str, Any]:
        result = self.orchestrator.cancel_task(run_id, task_id)
        if result.get("accepted"):
            with self._run_state_lock:
                run = self._active_run_states.get(run_id)
                task = run["tasks"].get(task_id) if run else None
                if task and task["status"] == "running":
                    task["status"] = "stopping"
        return result

    def stream_run(
        self,
        chat_id: str,
        mode: str,
        objective: str | None = None,
        agent_ids: list[str] | None = None,
        lead_agent_id: str | None = None,
        participant_ids: list[str] | None = None,
    ) -> Iterator[dict[str, Any]]:
        chat = self.storage.get_chat(chat_id)
        if chat is None:
            raise KeyError(f"Chat not found: {chat_id}")

        meeting_created = False
        run_chat_id = chat_id
        selected = list(agent_ids or chat.get("member_ids", []))
        if mode == "auto_meeting":
            if not lead_agent_id:
                raise ValueError("lead_agent_id is required for an auto meeting")
            chat = self._create_auto_meeting_chat(chat_id, lead_agent_id, objective or "")
            run_chat_id = chat["chat_id"]
            selected = list(chat.get("member_ids", []))
            self.create_meeting(run_chat_id, lead_agent_id, selected, objective or "", auto_run=False)
            meeting_created = True
            yield {
                "type": "meeting_created",
                "chat_id": run_chat_id,
                "source_chat_id": chat_id,
                "title": chat["title"],
                "agent_ids": selected,
            }
        elif mode == "meeting":
            if not lead_agent_id:
                raise ValueError("lead_agent_id is required for a meeting")
            selected = list(participant_ids or chat.get("member_ids", []))
            chat = self.create_meeting(chat_id, lead_agent_id, selected, objective or "", auto_run=False)
            selected = list(chat["meetings"][-1]["participant_ids"])
            meeting_created = True
        elif mode != "respond":
            raise ValueError(f"Unsupported stream mode: {mode}")

        if meeting_created:
            self._update_latest_meeting(run_chat_id, status="running", failed_agent_ids=[])

        run_agent_id = lead_agent_id or next(iter(selected), None)
        if not run_agent_id:
            raise ValueError("A lead agent is required to run the connected agent system")
        run_agent = self.get_agent(run_agent_id)
        if run_agent is None:
            raise ValueError(f"Agent not found: {run_agent_id}")

        run_id = uuid4().hex
        before_files = {
            path for path in self.project_root.rglob("*")
            if path.is_file() and ".git" not in path.parts
        }
        self.orchestrator.prepare_run(run_id)
        self._begin_run_state(run_id, run_chat_id, mode, run_agent_id)
        yield {
            "type": "run_started",
            "run_id": run_id,
            "chat_id": run_chat_id,
            "mode": mode,
            "agent_ids": selected,
            "lead_agent_id": run_agent_id,
        }
        failed_agent_ids: list[str] = []
        cancelled_agent_ids: list[str] = []
        run_cancelled = False
        buffers: dict[str, str] = {}
        labels: dict[str, str] = {}
        native_events = self.orchestrator.stream_agent_reply(
            agent=run_agent,
            chat=chat,
            transcript=list(chat.get("messages", [])),
            memory=self.storage.get_memory_json(run_agent_id),
            objective=objective,
            run_id=run_id,
        )
        for event in self._run_events(run_id, native_events):
            if event["type"] == "session_cancelled":
                run_cancelled = True
                continue
            agent_id = str(event.get("agent_id") or run_agent_id)
            event = {
                **event,
                "run_id": run_id,
                "task_id": str(event.get("task_id") or agent_id),
            }
            task_id = event["task_id"]
            registered_agent = self.get_agent(agent_id)
            registered_label = str((registered_agent or {}).get("title") or "")
            if event["type"] == "agent_started":
                labels[agent_id] = registered_label or str(event.get("agent_label") or agent_id)
                event = {**event, "agent_label": labels[agent_id]}
                buffers.setdefault(task_id, "")
                self._update_run_task(
                    run_id,
                    task_id,
                    agent_id,
                    labels[agent_id],
                    status="running",
                )
            elif event["type"] == "delta":
                delta = str(event.get("text") or "")
                buffers[task_id] = buffers.get(task_id, "") + delta
                self._update_run_task(
                    run_id,
                    task_id,
                    agent_id,
                    registered_label or labels.get(agent_id) or str(event.get("agent_label") or agent_id),
                    text_delta=delta,
                )
            elif event["type"] in {"agent_completed", "agent_failed", "agent_cancelled"}:
                failed = event["type"] == "agent_failed"
                cancelled = event["type"] == "agent_cancelled"
                body = str(
                    event.get("message")
                    or buffers.get(task_id)
                    or ("Stopped by user." if cancelled else "")
                ).strip()
                label = (
                    registered_label
                    or labels.get(agent_id)
                    or str(event.get("agent_label") or agent_id)
                )
                if event["type"] == "agent_failed":
                    event = {**event, "agent_label": label}
                if failed:
                    failed_agent_ids.append(agent_id)
                if cancelled:
                    cancelled_agent_ids.append(agent_id)
                interaction = None
                if event["type"] == "agent_completed" and body:
                    body, interaction = extract_choice_interaction(body)
                    if interaction:
                        event = {**event, "body": body, "interaction": interaction}
                self._update_run_task(
                    run_id,
                    task_id,
                    agent_id,
                    label,
                    status="failed" if failed else "cancelled" if cancelled else "completed",
                    text=body,
                )
                if body:
                    metadata = {
                        "run_id": run_id,
                        "objective": objective,
                        "source": (
                            "native_agent_error" if failed
                            else "native_agent_cancelled" if cancelled
                            else "native_agent_reply"
                        ),
                        "status": "failed" if failed else "cancelled" if cancelled else "completed",
                        "runner": self.orchestrator.backend,
                    }
                    if interaction:
                        metadata["interaction"] = interaction
                    chat = self.storage.append_message(
                        run_chat_id,
                        {
                            "author_type": "agent",
                            "author_id": agent_id,
                            "author_label": label,
                            "body": body,
                            "metadata": metadata,
                        },
                    )
                    if self.get_agent(agent_id):
                        try:
                            self.update_agent_memory(agent_id)
                        except Exception:
                            pass
            yield event

        created = []
        for path in self.project_root.rglob("*"):
            if path.is_file() and ".git" not in path.parts and path not in before_files:
                artifact = self.store_created_artifact(path)
                if artifact:
                    created.append(artifact)
        if created:
            saved_chat = self.storage.get_chat(run_chat_id)
            if saved_chat:
                for message in reversed(saved_chat.get("messages", [])):
                    if message.get("author_type") == "agent" and (message.get("metadata") or {}).get("run_id") == run_id:
                        message.setdefault("metadata", {})["attachments"] = created
                        self.storage.save_chat(saved_chat)
                        break

        if meeting_created:
            self._update_latest_meeting(
                run_chat_id,
                status=(
                    "cancelled" if run_cancelled
                    else "completed_with_errors" if failed_agent_ids
                    else "completed"
                ),
                failed_agent_ids=failed_agent_ids,
                cancelled_agent_ids=cancelled_agent_ids,
            )
        if run_cancelled:
            yield {"type": "run_cancelled", "run_id": run_id, "chat_id": run_chat_id}
        else:
            yield {"type": "run_completed", "run_id": run_id, "chat_id": run_chat_id}

    def _update_latest_meeting(self, chat_id: str, **updates: Any) -> None:
        chat = self.storage.get_chat(chat_id)
        if chat and chat.get("meetings"):
            chat["meetings"][-1].update(updates)
            self.storage.save_chat(chat)

    def create_meeting(
        self,
        chat_id: str,
        lead_agent_id: str,
        participant_ids: list[str],
        objective: str,
        auto_run: bool = True,
    ) -> dict[str, Any]:
        chat = self.storage.get_chat(chat_id)
        if chat is None:
            raise KeyError(f"Chat not found: {chat_id}")
        if self.get_agent(lead_agent_id) is None:
            raise ValueError(f"Agent not found: {lead_agent_id}")
        available = {agent["agent_id"] for agent in self.list_agents()}
        participants = [
            agent_id
            for agent_id in dict.fromkeys([*participant_ids, lead_agent_id])
            if agent_id in available
        ]
        chat["member_ids"] = list(dict.fromkeys([*chat.get("member_ids", []), *participants]))
        if len(chat["member_ids"]) > 1:
            chat["kind"] = "group"
        self.storage.save_chat(chat)
        meeting = {
            "lead_agent_id": lead_agent_id,
            "participant_ids": participants,
            "objective": objective,
            "auto_run": auto_run,
            "status": "planned",
        }
        chat = self.storage.append_meeting(chat_id, meeting)
        self._append_system_message(
            chat_id,
            f"Meeting scheduled by {lead_agent_id} with {', '.join(participants)}. Objective: {objective}",
        )
        if auto_run:
            self._update_latest_meeting(chat_id, status="running", failed_agent_ids=[])
            events = list(self.stream_run(
                chat_id,
                mode="respond",
                objective=objective,
                agent_ids=[lead_agent_id],
                lead_agent_id=lead_agent_id,
            ))
            failed = [event["agent_id"] for event in events if event["type"] == "agent_failed"]
            self._update_latest_meeting(
                chat_id,
                status="completed_with_errors" if failed else "completed",
                failed_agent_ids=failed,
            )
            chat = self.storage.get_chat(chat_id) or chat
        return chat

    def auto_meeting(self, chat_id: str, lead_agent_id: str, objective: str) -> dict[str, Any]:
        chat = self._create_auto_meeting_chat(chat_id, lead_agent_id, objective)
        return self.create_meeting(
            chat_id=chat["chat_id"],
            lead_agent_id=lead_agent_id,
            participant_ids=chat["member_ids"],
            objective=objective,
            auto_run=True,
        )

    def _create_auto_meeting_chat(self, source_chat_id: str, lead_agent_id: str, objective: str) -> dict[str, Any]:
        source_chat = self.storage.get_chat(source_chat_id)
        if source_chat is None:
            raise KeyError(f"Chat not found: {source_chat_id}")
        lead = self.get_agent(lead_agent_id)
        if lead is None:
            raise ValueError(f"Agent not found: {lead_agent_id}")

        participants = list(dict.fromkeys([*source_chat.get("member_ids", []), lead_agent_id]))
        title_objective = " ".join(objective.split()) or "New discussion"
        if len(title_objective) > 72:
            title_objective = f"{title_objective[:69].rstrip()}..."
        meeting_chat = self.create_chat(
            title=f"{lead['title']} Meeting: {title_objective}",
            member_ids=participants,
            kind="group",
        )
        if objective.strip():
            meeting_chat = self.add_user_message(meeting_chat["chat_id"], objective)
        self._append_system_message(
            source_chat_id,
            f"Meeting created: {meeting_chat['title']}",
        )
        return meeting_chat

    def suggest_participants(self, lead_agent_id: str, objective: str) -> list[str]:
        available = {agent["agent_id"] for agent in self.list_agents()}
        return self.architecture.suggest_participants(lead_agent_id, objective, available)

    def update_agent_memory(self, agent_id: str) -> None:
        agent = self.get_agent(agent_id)
        if agent is None:
            return
        chats = [chat for chat in self.storage.list_chats() if agent_id in chat.get("member_ids", [])]
        authored = []
        referenced = []
        for chat in chats:
            for msg in chat.get("messages", []):
                if msg.get("author_id") == agent_id:
                    authored.append((chat, msg))
                elif msg.get("author_type") == "user":
                    referenced.append((chat, msg))

        recent_authored = authored[-8:]
        recent_user = referenced[-8:]
        channel_counter = Counter(chat["title"] for chat, _ in authored)
        top_channels = [title for title, _count in channel_counter.most_common(5)]
        last_response = recent_authored[-1][1]["body"] if recent_authored else ""
        recent_objectives = [
            msg["body"].strip().splitlines()[0][:160]
            for _chat, msg in recent_user[-5:]
            if msg.get("body")
        ]

        memory_json = {
            "agent_id": agent_id,
            "updated_at": utc_now(),
            "title": agent["title"],
            "summary": (
                f"{agent['title']} has participated in {len(chats)} chats, authored {len(authored)} messages, "
                f"and most recently worked on: {recent_objectives[-1] if recent_objectives else 'no recorded objective yet'}."
            ),
            "stats": {
                "chat_count": len(chats),
                "message_count": len(authored),
            },
            "recent_channels": top_channels,
            "recent_objectives": recent_objectives,
            "last_response_preview": last_response[:500],
            "source_path": agent["source_path"],
        }

        ledger_lines = []
        for chat, msg in recent_authored:
            ledger_lines.append(f"- {msg['created_at']} | {chat['title']} | {msg['body'][:180].replace(chr(10), ' ')}")
        if not ledger_lines:
            ledger_lines.append("- No authored messages yet.")

        channel_lines = [f"- {item}" for item in top_channels] or ["- None yet."]
        objective_lines = [f"- {item}" for item in recent_objectives] or ["- None yet."]
        markdown = "\n".join(
            [
                f"# Memory: {agent['title']}",
                "",
                f"- Agent ID: `{agent_id}`",
                f"- Source: `{agent['source_path']}`",
                f"- Updated: `{memory_json['updated_at']}`",
                "",
                "## Summary",
                "",
                memory_json["summary"],
                "",
                "## Recent Channels",
                "",
                *channel_lines,
                "",
                "## Recent Objectives",
                "",
                *objective_lines,
                "",
                "## Last Response Preview",
                "",
                last_response[:1000] if last_response else "No response yet.",
                "",
                "## Message Ledger",
                "",
                *ledger_lines,
            ]
        )
        self.storage.write_memory(agent_id, memory_json, markdown)

    def _append_system_message(self, chat_id: str, body: str) -> None:
        self.storage.append_message(
            chat_id,
            {
                "author_type": "system",
                "author_id": "system",
                "author_label": "System",
                "body": body,
                "metadata": {},
            },
        )
