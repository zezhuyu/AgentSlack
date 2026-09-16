from __future__ import annotations

from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

import pytest

from agent_slack.app import AgentSlackApp


def _write_agent(project_root: Path, agent_id: str, title: str) -> None:
    path = project_root / ".claude" / "agents" / f"{agent_id}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\nname: {agent_id}\nsummary: {title} responsibility.\ntools:\n  - Read\n---\n"
        f"# {title}\n\nPerform host-defined work.\n",
        encoding="utf-8",
    )


def _app(tmp_path: Path, monkeypatch) -> AgentSlackApp:
    monkeypatch.setenv("AGENT_SLACK_CLI", "offline")
    project_root = tmp_path / "project"
    app_root = tmp_path / "agent-slack"
    project_root.mkdir()
    app_root.mkdir()
    _write_agent(project_root, "coordinator", "System Coordinator")
    _write_agent(project_root, "reviewer", "Review Agent")
    return AgentSlackApp(project_root, app_root)


def test_direct_response_runs_one_connected_agent_session(tmp_path: Path, monkeypatch) -> None:
    app = _app(tmp_path, monkeypatch)
    calls = []

    def stream(**kwargs):
        calls.append(kwargs)
        yield {"type": "agent_started", "agent_id": "coordinator", "agent_label": "System Coordinator"}
        yield {"type": "delta", "agent_id": "coordinator", "text": "Final host answer."}
        yield {"type": "agent_completed", "agent_id": "coordinator"}

    app.orchestrator.stream_agent_reply = stream
    chat = app.create_chat("Coordinator", ["coordinator"], kind="direct")
    app.add_user_message(chat["chat_id"], "Review this")

    events = list(app.stream_run(chat["chat_id"], mode="respond", objective="Review this"))

    assert len(calls) == 1
    assert calls[0]["agent"]["agent_id"] == "coordinator"
    assert events[0]["lead_agent_id"] == "coordinator"
    assert events[0]["chat_id"] == chat["chat_id"]
    assert app.get_chat(chat["chat_id"])["messages"][-1]["body"] == "Final host answer."


def test_uploaded_files_are_stored_with_trusted_absolute_paths_and_attached_to_messages(
    tmp_path: Path, monkeypatch
) -> None:
    app = _app(tmp_path, monkeypatch)
    chat = app.create_chat("Coordinator", ["coordinator"], kind="direct")

    upload = app.store_upload("../Quarterly Report.pdf", "application/pdf", b"report-data")
    updated = app.add_user_message(
        chat["chat_id"],
        "Summarize the attached report",
        attachment_ids=[upload["attachment_id"]],
    )

    assert Path(upload["path"]).is_absolute()
    assert Path(upload["path"]).read_bytes() == b"report-data"
    assert Path(upload["path"]).name == "Quarterly Report.pdf"
    assert updated["messages"][-1]["metadata"]["attachments"] == [upload]


def test_message_rejects_unknown_attachment_ids(tmp_path: Path, monkeypatch) -> None:
    app = _app(tmp_path, monkeypatch)
    chat = app.create_chat("Coordinator", ["coordinator"], kind="direct")

    with pytest.raises(ValueError, match="Unknown attachment"):
        app.add_user_message(
            chat["chat_id"],
            "Inspect this",
            attachment_ids=["not-an-upload"],
        )


def test_voice_capabilities_and_idempotent_message_submission(tmp_path: Path, monkeypatch) -> None:
    app = _app(tmp_path, monkeypatch)
    chat = app.create_chat("Coordinator", ["coordinator"], kind="direct")

    first = app.add_user_message(chat["chat_id"], "Use voice", idempotency_key="voice-1")
    second = app.add_user_message(chat["chat_id"], "Use voice again", idempotency_key="voice-1")

    assert app.voice_capabilities()["enabled"] is True
    assert second == first
    assert [m["body"] for m in second["messages"] if m["author_type"] == "user"] == ["Use voice"]


def test_upload_path_is_opaque_and_previewable(tmp_path: Path, monkeypatch) -> None:
    app = _app(tmp_path, monkeypatch)
    upload = app.store_upload("preview.txt", "text/plain", b"hello")

    resolved = app.get_upload_path(upload["attachment_id"])
    assert resolved is not None
    path, metadata = resolved
    assert path.read_bytes() == b"hello"
    assert metadata["filename"] == "preview.txt"
    assert app.get_upload_path("../metadata") is None


def test_created_artifact_capture_excludes_hidden_and_secret_files(tmp_path: Path, monkeypatch) -> None:
    app = _app(tmp_path, monkeypatch)
    assert app.store_created_artifact(app.project_root / ".env") is None
    secret = app.project_root / "credentials.pem"
    secret.write_text("private", encoding="utf-8")
    assert app.store_created_artifact(secret) is None


def test_resolve_workspace_file_previews_cited_paths_inside_the_project(tmp_path: Path, monkeypatch) -> None:
    app = _app(tmp_path, monkeypatch)
    cited = app.project_root / "notes" / "report.md"
    cited.parent.mkdir(parents=True)
    cited.write_text("findings", encoding="utf-8")

    resolved = app.resolve_workspace_file(f"{cited}:122")

    assert resolved == cited


def test_resolve_workspace_file_rejects_paths_outside_the_project_or_secrets(tmp_path: Path, monkeypatch) -> None:
    app = _app(tmp_path, monkeypatch)
    outside = tmp_path / "outside.md"
    outside.write_text("nope", encoding="utf-8")
    secret = app.project_root / "credentials.pem"
    secret.write_text("private", encoding="utf-8")

    assert app.resolve_workspace_file(str(outside)) is None
    assert app.resolve_workspace_file(str(secret)) is None
    assert app.resolve_workspace_file("") is None
    assert app.resolve_workspace_file(str(app.project_root / "missing.md")) is None


def test_agent_choice_question_is_persisted_and_answered_from_dynamic_options(
    tmp_path: Path, monkeypatch
) -> None:
    app = _app(tmp_path, monkeypatch)

    def stream(**_kwargs):
        yield {"type": "agent_started", "agent_id": "coordinator", "agent_label": "System Coordinator"}
        yield {
            "type": "delta",
            "agent_id": "coordinator",
            "text": (
                "The specification is ready."
                '<agent-slack-question>{"prompt":"Approve it?","multiple":false,'
                '"options":[{"id":"ship","label":"Approve","description":"Start work"},'
                '{"id":"revise","label":"Request changes"}]}</agent-slack-question>'
            ),
        }
        yield {"type": "agent_completed", "agent_id": "coordinator"}

    app.orchestrator.stream_agent_reply = stream
    chat = app.create_chat("Coordinator", ["coordinator"], kind="direct")
    app.add_user_message(chat["chat_id"], "Write a specification")

    events = list(app.stream_run(chat["chat_id"], mode="respond", objective="Write a specification"))
    saved = app.get_chat(chat["chat_id"])
    question_message = saved["messages"][-1]
    interaction = question_message["metadata"]["interaction"]

    assert question_message["body"] == "The specification is ready."
    assert interaction["prompt"] == "Approve it?"
    assert [option["label"] for option in interaction["options"]] == ["Approve", "Request changes"]
    completed = next(event for event in events if event["type"] == "agent_completed")
    assert completed["interaction"]["question_id"] == interaction["question_id"]

    answered = app.answer_question(chat["chat_id"], interaction["question_id"], ["ship"])
    answered_interaction = answered["messages"][-2]["metadata"]["interaction"]
    answer_message = answered["messages"][-1]

    assert answered_interaction["status"] == "answered"
    assert answered_interaction["selected_option_ids"] == ["ship"]
    assert answer_message["author_type"] == "user"
    assert answer_message["body"] == 'Response to "Approve it?": Approve'
    assert answer_message["metadata"]["interaction_response"]["question_id"] == interaction["question_id"]


def test_choice_answer_validates_agent_supplied_options_and_cardinality(
    tmp_path: Path, monkeypatch
) -> None:
    app = _app(tmp_path, monkeypatch)
    chat = app.create_chat("Coordinator", ["coordinator"], kind="direct")
    app.storage.append_message(
        chat["chat_id"],
        {
            "author_type": "agent",
            "author_id": "coordinator",
            "author_label": "System Coordinator",
            "body": "Choose a path.",
            "metadata": {
                "interaction": {
                    "kind": "choice",
                    "question_id": "question-1",
                    "prompt": "Choose a path",
                    "multiple": False,
                    "status": "pending",
                    "selected_option_ids": [],
                    "options": [
                        {"id": "safe", "label": "Safe"},
                        {"id": "fast", "label": "Fast"},
                    ],
                }
            },
        },
    )

    with pytest.raises(ValueError, match="exactly one"):
        app.answer_question(chat["chat_id"], "question-1", ["safe", "fast"])
    with pytest.raises(ValueError, match="Unknown option"):
        app.answer_question(chat["chat_id"], "question-1", ["missing"])


def test_choice_question_accepts_only_one_concurrent_answer(
    tmp_path: Path, monkeypatch
) -> None:
    app = _app(tmp_path, monkeypatch)
    chat = app.create_chat("Coordinator", ["coordinator"], kind="direct")
    app.storage.append_message(
        chat["chat_id"],
        {
            "author_type": "agent",
            "author_id": "coordinator",
            "author_label": "System Coordinator",
            "body": "Choose.",
            "metadata": {
                "interaction": {
                    "kind": "choice",
                    "question_id": "question-race",
                    "prompt": "Choose",
                    "multiple": False,
                    "status": "pending",
                    "selected_option_ids": [],
                    "options": [
                        {"id": "one", "label": "One"},
                        {"id": "two", "label": "Two"},
                    ],
                }
            },
        },
    )

    def answer(option_id: str) -> str:
        try:
            app.answer_question(chat["chat_id"], "question-race", [option_id])
            return "accepted"
        except ValueError:
            return "rejected"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(answer, ["one", "two"]))

    saved = app.get_chat(chat["chat_id"])
    responses = [
        message for message in saved["messages"]
        if (message.get("metadata") or {}).get("interaction_response")
    ]
    assert sorted(results) == ["accepted", "rejected"]
    assert len(responses) == 1


def test_delete_chat_removes_chat_and_refreshes_agent_memory(tmp_path: Path, monkeypatch) -> None:
    app = _app(tmp_path, monkeypatch)
    chat = app.create_chat("Temporary Chat", ["coordinator"], kind="direct")
    app.add_user_message(chat["chat_id"], "temporary message")

    deleted = app.delete_chat(chat["chat_id"])

    assert deleted == {"deleted": True, "chat_id": chat["chat_id"]}
    assert app.get_chat(chat["chat_id"]) is None
    memory = app.storage.get_memory_json("coordinator")
    assert "Temporary Chat" not in memory.get("recent_channels", [])
    assert memory["stats"]["chat_count"] == 0


def test_delete_chat_rejects_chats_with_active_runs(tmp_path: Path, monkeypatch) -> None:
    app = _app(tmp_path, monkeypatch)
    chat = app.create_chat("Temporary Chat", ["coordinator"], kind="direct")
    app._begin_run_state("run-1", chat["chat_id"], "respond", "coordinator")

    try:
        with pytest.raises(ValueError, match="while a run is still active"):
            app.delete_chat(chat["chat_id"])
    finally:
        with app._run_state_lock:
            app._active_run_states.clear()

    assert app.get_chat(chat["chat_id"]) is not None


def test_native_subagent_results_are_streamed_and_saved_before_final_answer(
    tmp_path: Path, monkeypatch
) -> None:
    app = _app(tmp_path, monkeypatch)

    def stream(**_kwargs):
        yield {
            "type": "agent_started",
            "agent_id": "reviewer",
            "agent_label": "Review the release evidence",
        }
        yield {"type": "delta", "agent_id": "reviewer", "text": "Independent review result."}
        yield {"type": "agent_completed", "agent_id": "reviewer"}
        yield {"type": "agent_started", "agent_id": "coordinator", "agent_label": "System Coordinator"}
        yield {"type": "delta", "agent_id": "coordinator", "text": "Final synthesis."}
        yield {"type": "agent_completed", "agent_id": "coordinator"}

    app.orchestrator.stream_agent_reply = stream
    direct = app.create_chat("Coordinator", ["coordinator"], kind="direct")
    events = list(app.stream_run(
        direct["chat_id"],
        mode="auto_meeting",
        lead_agent_id="coordinator",
        objective="Review the release",
    ))

    created = events[0]
    assert created["agent_ids"] == ["coordinator"]
    saved = app.get_chat(created["chat_id"])
    agent_messages = [message for message in saved["messages"] if message["author_type"] == "agent"]
    assert [message["author_id"] for message in agent_messages] == ["reviewer", "coordinator"]
    assert [message["body"] for message in agent_messages] == [
        "Independent review result.",
        "Final synthesis.",
    ]
    assert [message["author_label"] for message in agent_messages] == [
        "Review Agent",
        "System Coordinator",
    ]
    reviewer_started = next(
        event for event in events
        if event["type"] == "agent_started" and event["agent_id"] == "reviewer"
    )
    assert reviewer_started["agent_label"] == "Review Agent"
    assert saved["meetings"][-1]["status"] == "completed"


def test_manual_invites_are_context_not_frontend_execution_policy(tmp_path: Path, monkeypatch) -> None:
    app = _app(tmp_path, monkeypatch)
    called_agents = []

    def stream(**kwargs):
        called_agents.append(kwargs["agent"]["agent_id"])
        yield {"type": "agent_started", "agent_id": "coordinator", "agent_label": "System Coordinator"}
        yield {"type": "delta", "agent_id": "coordinator", "text": "Host-owned result."}
        yield {"type": "agent_completed", "agent_id": "coordinator"}

    app.orchestrator.stream_agent_reply = stream
    chat = app.create_chat("Group", ["coordinator", "reviewer"], kind="group")
    updated = app.create_meeting(
        chat["chat_id"],
        lead_agent_id="coordinator",
        participant_ids=["reviewer"],
        objective="Review together",
        auto_run=True,
    )

    assert called_agents == ["coordinator"]
    assert updated["member_ids"] == ["coordinator", "reviewer"]
    assert updated["meetings"][-1]["participant_ids"] == ["reviewer", "coordinator"]


def test_native_failure_is_persisted_and_marks_meeting(tmp_path: Path, monkeypatch) -> None:
    app = _app(tmp_path, monkeypatch)

    def stream(**_kwargs):
        yield {
            "type": "agent_failed",
            "agent_id": "coordinator",
            "agent_label": "System Coordinator",
            "message": "Claude session failed.",
        }

    app.orchestrator.stream_agent_reply = stream
    chat = app.create_chat("Coordinator", ["coordinator"], kind="direct")
    updated = app.create_meeting(
        chat["chat_id"], "coordinator", [], "Review", auto_run=True
    )

    assert updated["meetings"][-1]["status"] == "completed_with_errors"
    assert updated["meetings"][-1]["failed_agent_ids"] == ["coordinator"]
    assert updated["messages"][-1]["metadata"]["source"] == "native_agent_error"


def test_cancelled_native_agent_is_persisted_without_marking_failure(
    tmp_path: Path, monkeypatch
) -> None:
    app = _app(tmp_path, monkeypatch)

    def stream(**_kwargs):
        yield {
            "type": "agent_started",
            "agent_id": "reviewer",
            "agent_label": "Review Agent",
            "task_id": "task-reviewer",
        }
        yield {
            "type": "agent_cancelled",
            "agent_id": "reviewer",
            "agent_label": "Review Agent",
            "task_id": "task-reviewer",
            "message": "Stopped by user.",
        }
        yield {"type": "session_cancelled"}

    app.orchestrator.stream_agent_reply = stream
    direct = app.create_chat("Coordinator", ["coordinator"], kind="direct")
    events = list(app.stream_run(
        direct["chat_id"],
        mode="auto_meeting",
        lead_agent_id="coordinator",
        objective="Review the release",
    ))

    created = events[0]
    saved = app.get_chat(created["chat_id"])
    cancelled = next(message for message in saved["messages"] if message["author_id"] == "reviewer")
    assert cancelled["body"] == "Stopped by user."
    assert cancelled["metadata"]["status"] == "cancelled"
    assert cancelled["metadata"]["source"] == "native_agent_cancelled"
    assert saved["meetings"][-1]["status"] == "cancelled"
    assert events[-1]["type"] == "run_cancelled"


def test_reload_keeps_orchestrator_that_owns_an_active_run(tmp_path: Path, monkeypatch) -> None:
    app = _app(tmp_path, monkeypatch)
    original = app.orchestrator
    original.prepare_run("run-1")

    app.reload_host_configuration()

    assert app.orchestrator is original
    assert app.orchestrator.has_active_runs is True


def test_active_run_state_survives_without_the_streaming_client(tmp_path: Path, monkeypatch) -> None:
    app = _app(tmp_path, monkeypatch)

    def stream(**_kwargs):
        yield {
            "type": "agent_started",
            "agent_id": "reviewer",
            "agent_label": "Review Agent",
            "task_id": "task-reviewer",
        }
        yield {
            "type": "delta",
            "agent_id": "reviewer",
            "task_id": "task-reviewer",
            "text": "Still researching.",
        }
        yield {
            "type": "agent_completed",
            "agent_id": "reviewer",
            "task_id": "task-reviewer",
        }

    app.orchestrator.stream_agent_reply = stream
    chat = app.create_chat("Coordinator", ["coordinator"], kind="direct")
    events = app.stream_run(chat["chat_id"], mode="respond", objective="Review this")

    started = next(events)
    assert started["type"] == "run_started"
    assert next(events)["type"] == "agent_started"
    assert next(events)["type"] == "delta"

    active = app.list_active_runs(chat["chat_id"])
    assert len(active) == 1
    assert active[0]["run_id"] == started["run_id"]
    assert active[0]["status"] == "running"
    assert active[0]["tasks"] == [
        {
            "task_id": "task-reviewer",
            "agent_id": "reviewer",
            "agent_label": "Review Agent",
            "status": "running",
            "text": "Still researching.",
        }
    ]

    assert [event["type"] for event in events] == ["agent_completed", "run_completed"]
    assert app.list_active_runs(chat["chat_id"]) == []


def test_cancellation_marks_reconnect_state_as_stopping(tmp_path: Path, monkeypatch) -> None:
    app = _app(tmp_path, monkeypatch)
    chat = app.create_chat("Coordinator", ["coordinator"], kind="direct")
    app._begin_run_state("run-1", chat["chat_id"], "respond", "coordinator")
    app._update_run_task("run-1", "task-1", "reviewer", "Review Agent")
    app.orchestrator.cancel_task = lambda run_id, task_id: {
        "accepted": True,
        "run_id": run_id,
        "task_id": task_id,
        "status": "stopping",
    }
    app.orchestrator.cancel_run = lambda run_id: {
        "accepted": True,
        "run_id": run_id,
        "status": "stopping",
    }

    app.cancel_task("run-1", "task-1")
    assert app.list_active_runs(chat["chat_id"])[0]["tasks"][0]["status"] == "stopping"

    app.cancel_run("run-1")
    assert app.list_active_runs(chat["chat_id"])[0]["status"] == "stopping"


def test_plain_project_defaults_to_claude_without_an_agent_manifest(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.delenv("AGENT_SLACK_CLI", raising=False)
    monkeypatch.setattr("agent_slack.orchestrator.shutil.which", lambda name: f"/bin/{name}")
    project_root = tmp_path / "plain-project"
    app_root = tmp_path / "agent-slack"
    project_root.mkdir()
    app_root.mkdir()

    app = AgentSlackApp(project_root, app_root)

    assert app.orchestrator.backend == "claude"
    assert [agent["kind"] for agent in app.list_agents()] == ["project"]


def test_chat_summary_excludes_internal_system_messages(tmp_path: Path, monkeypatch) -> None:
    app = _app(tmp_path, monkeypatch)
    chat = app.create_chat("Coordinator", ["coordinator"], kind="direct")

    initial = next(item for item in app.list_chats() if item["chat_id"] == chat["chat_id"])
    assert initial["message_count"] == 0
    assert initial["last_message_preview"] == ""

    app.add_user_message(chat["chat_id"], "Visible user message")
    updated = next(item for item in app.list_chats() if item["chat_id"] == chat["chat_id"])
    assert updated["message_count"] == 1
    assert updated["last_message_preview"] == "Visible user message"
