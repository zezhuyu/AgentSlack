from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from .architecture import AgentSystemArchitecture
from .discovery import AgentDiscovery
from .orchestrator import CliOrchestrator
from .storage import AgentSlackStorage, utc_now


class AgentSlackApp:
    def __init__(self, project_root: Path, app_root: Path, data_root: Path | None = None):
        self.project_root = project_root
        self.app_root = app_root
        self.data_root = data_root or self.app_root / "data"
        self.static_root = self.app_root / "static"
        self.discovery = AgentDiscovery(project_root)
        self.architecture = AgentSystemArchitecture.load(project_root)
        self.storage = AgentSlackStorage(self.data_root)
        self.workspace_name = self.project_root.name
        self.orchestrator = CliOrchestrator(self.workspace_name, self.project_root)
        self.bootstrap()

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

    def list_chats(self) -> list[dict[str, Any]]:
        chats = self.storage.list_chats()
        agents = {agent["agent_id"]: agent for agent in self.list_agents()}
        summaries = []
        for chat in chats:
            messages = chat.get("messages", [])
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

    def create_chat(self, title: str, member_ids: list[str], kind: str = "group") -> dict[str, Any]:
        chat = self.storage.create_chat(title=title, member_ids=member_ids, kind=kind)
        self._append_system_message(chat["chat_id"], f"Chat created for: {', '.join(member_ids)}")
        return self.storage.get_chat(chat["chat_id"]) or chat

    def add_user_message(self, chat_id: str, body: str, author_label: str = "You") -> dict[str, Any]:
        chat = self.storage.append_message(
            chat_id,
            {
                "author_type": "user",
                "author_id": "user",
                "author_label": author_label,
                "body": body.strip(),
                "metadata": {},
            },
        )
        for member_id in chat.get("member_ids", []):
            self.update_agent_memory(member_id)
        return chat

    def run_agents(self, chat_id: str, agent_ids: list[str] | None = None, objective: str | None = None) -> dict[str, Any]:
        chat = self.storage.get_chat(chat_id)
        if chat is None:
            raise KeyError(f"Chat not found: {chat_id}")

        selected = agent_ids or chat.get("member_ids", [])
        for agent_id in selected:
            agent = self.get_agent(agent_id)
            if agent is None:
                continue
            memory = self.storage.get_memory_json(agent_id)
            reply = self.orchestrator.generate_agent_reply(
                agent=agent,
                chat=chat,
                transcript=chat.get("messages", []),
                memory=memory,
                objective=objective,
            )
            chat = self.storage.append_message(
                chat_id,
                {
                    "author_type": "agent",
                    "author_id": agent_id,
                    "author_label": agent["title"],
                    "body": reply,
                    "metadata": {"objective": objective, "source": "agent_reply"},
                },
            )
            self.update_agent_memory(agent_id)
        return chat

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
            self.create_meeting(chat_id, lead_agent_id, selected, objective or "", auto_run=False)
            meeting_created = True
        elif mode != "respond":
            raise ValueError(f"Unsupported stream mode: {mode}")

        if lead_agent_id:
            selected = [agent_id for agent_id in selected if agent_id != lead_agent_id] + [lead_agent_id]

        yield {"type": "run_started", "mode": mode, "agent_ids": selected}
        for agent_id in selected:
            agent = self.get_agent(agent_id)
            if agent is None:
                continue
            chat = self.storage.get_chat(run_chat_id) or chat
            yield {
                "type": "agent_started",
                "agent_id": agent_id,
                "agent_label": agent["title"],
            }
            reply = self.orchestrator.generate_agent_reply(
                agent=agent,
                chat=chat,
                transcript=chat.get("messages", []),
                memory=self.storage.get_memory_json(agent_id),
                objective=objective,
            )
            for chunk in self._reply_chunks(reply):
                yield {"type": "delta", "agent_id": agent_id, "text": chunk}
            chat = self.storage.append_message(
                run_chat_id,
                {
                    "author_type": "agent",
                    "author_id": agent_id,
                    "author_label": agent["title"],
                    "body": reply,
                    "metadata": {"objective": objective, "source": "agent_reply"},
                },
            )
            self.update_agent_memory(agent_id)
            yield {"type": "agent_completed", "agent_id": agent_id}

        if meeting_created:
            chat = self.storage.get_chat(run_chat_id) or chat
            if chat.get("meetings"):
                chat["meetings"][-1]["status"] = "completed"
                self.storage.save_chat(chat)
        yield {"type": "run_completed", "chat_id": run_chat_id}

    @staticmethod
    def _reply_chunks(reply: str, target_size: int = 28) -> Iterator[str]:
        chunk = ""
        for token in re.findall(r"\S+\s*|\s+", reply):
            chunk += token
            if len(chunk) >= target_size:
                yield chunk
                chunk = ""
        if chunk:
            yield chunk

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
        meeting = {
            "lead_agent_id": lead_agent_id,
            "participant_ids": participant_ids,
            "objective": objective,
            "auto_run": auto_run,
            "status": "planned",
        }
        chat = self.storage.append_meeting(chat_id, meeting)
        self._append_system_message(
            chat_id,
            f"Meeting scheduled by {lead_agent_id} with {', '.join(participant_ids)}. Objective: {objective}",
        )
        if auto_run:
            ordered = [agent_id for agent_id in participant_ids if agent_id != lead_agent_id] + [lead_agent_id]
            chat = self.run_agents(chat_id, ordered, objective=objective)
            chat["meetings"][-1]["status"] = "completed"
            self.storage.save_chat(chat)
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

        suggested = self.suggest_participants(lead_agent_id, objective)
        participants = sorted(set(source_chat.get("member_ids", []) + suggested + [lead_agent_id]))
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
