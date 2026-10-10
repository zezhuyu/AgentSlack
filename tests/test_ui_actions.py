from pathlib import Path
from uuid import uuid4

import pytest

from agent_slack.app import AgentSlackApp
from agent_slack.generative_ui import bind_ui_capabilities
from agent_slack.ui_actions import UIActionError, validate_form_args


def make_app(tmp_path: Path, monkeypatch) -> AgentSlackApp:
    monkeypatch.setenv("AGENT_SLACK_CLI", "offline")
    project = tmp_path / "project"
    app_root = tmp_path / "app"
    agent_file = project / ".claude" / "agents" / "analyst.md"
    agent_file.parent.mkdir(parents=True)
    agent_file.write_text(
        "---\nname: analyst\nsummary: Analysis\ntools:\n - Read\n---\n# Analyst\nAnalyze.\n",
        encoding="utf-8",
    )
    return AgentSlackApp(project, app_root)


def add_ui_message(app, chat, spec):
    approvals = bind_ui_capabilities(spec)
    ui_message_id = uuid4().hex[:12]
    app.storage.append_message(chat["chat_id"], {
        "message_id": ui_message_id,
        "author_type": "agent",
        "author_id": "analyst",
        "body": "Here is the dashboard.",
        "metadata": {
            "agent_ui": {
                "schemaVersion": "1", "messageId": ui_message_id,
                "workspaceId": app.workspace_key, "agentId": "analyst",
                "runId": "origin-run", "taskId": "origin-task",
                "content": {"text": "Here is the dashboard.", "ui": spec, "approvals": approvals},
                "status": "ready",
            },
        },
    })
    saved = app.get_chat(chat["chat_id"])["messages"][-1]
    return saved


def test_registered_run_action_is_chat_scoped_and_idempotent(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)
    chat = app.create_chat("Analysis", ["analyst"], kind="direct")
    spec = {"schemaVersion": "1", "root": {
        "type": "Button", "props": {
            "label": "Run analysis",
            "action": {"type": "run_analysis", "agentIds": ["analyst"], "objective": "Review results"},
        },
    }}
    message = add_ui_message(app, chat, spec)
    calls = []

    def stream(*args, **kwargs):
        calls.append((args, kwargs))
        yield {"type": "run_started", "run_id": "ui-run-1"}
        yield {"type": "run_completed", "run_id": "ui-run-1"}

    app.stream_run = stream
    node_id = message["metadata"]["agent_ui"]["content"]["ui"]["root"]["props"]["id"]
    events = list(app.stream_ui_action(chat["chat_id"], message["message_id"], "run_analysis", node_id, {}, "action-key-0001"))
    duplicate = list(app.stream_ui_action(chat["chat_id"], message["message_id"], "run_analysis", node_id, {}, "action-key-0001"))

    assert [event["type"] for event in events] == ["ui.action.requested", "run_started", "run_completed", "ui.action.completed"]
    assert duplicate[-1]["duplicate"] is True
    assert len(calls) == 1
    assert calls[0][1]["agent_ids"] == ["analyst"]
    assert calls[0][1]["objective"] == "Review results"
    stored = app.get_chat(chat["chat_id"])["ui_action_requests"]["action-key-0001"]
    assert stored["status"] == "completed"
    assert stored["run_id"] == "ui-run-1"


def test_action_cannot_target_agent_outside_chat_or_invent_registry_operation(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)
    chat = app.create_chat("Analysis", ["analyst"], kind="direct")
    spec = {"schemaVersion": "1", "root": {
        "type": "Button", "props": {"label": "Run", "action": {
            "type": "run_analysis", "agentIds": ["outside-agent"], "objective": "Do work",
        }},
    }}
    message = add_ui_message(app, chat, spec)
    node_id = message["metadata"]["agent_ui"]["content"]["ui"]["root"]["props"]["id"]
    with pytest.raises(UIActionError, match="outside this chat"):
        list(app.stream_ui_action(chat["chat_id"], message["message_id"], "run_analysis", node_id, {}, "action-key-0002"))
    with pytest.raises(UIActionError, match="Unsupported UI action"):
        list(app.stream_ui_action(chat["chat_id"], message["message_id"], "delete_files", node_id, {}, "action-key-0003"))


def test_approval_card_requires_explicit_decision_and_rechecks_target(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)
    chat = app.create_chat("Analysis", ["analyst"], kind="direct")
    spec = {"schemaVersion": "1", "root": {
        "type": "ApprovalCard", "props": {
            "title": "Run report", "summary": "Analyze the current data.", "impact": "Read-only analysis",
            "action": {"type": "run_analysis", "agentIds": ["analyst"], "objective": "Analyze current data"},
        },
    }}
    message = add_ui_message(app, chat, spec)
    approval_id = message["metadata"]["agent_ui"]["content"]["ui"]["root"]["props"]["approvalId"]
    runs = []

    def stream(*_args, **kwargs):
        runs.append(kwargs)
        yield {"type": "run_started", "run_id": "approved-run"}

    app.stream_run = stream
    events = list(app.stream_ui_action(chat["chat_id"], message["message_id"], "approve", approval_id, {}, "approval-key-01"))
    persisted = app.get_chat(chat["chat_id"])["messages"][-1]["metadata"]["agent_ui"]

    assert events[-1]["type"] == "ui.action.completed"
    assert runs[0]["agent_ids"] == ["analyst"]
    assert persisted["content"]["approvals"][approval_id]["status"] == "approved"
    assert persisted["content"]["ui"]["root"]["props"]["status"] == "approved"
    with pytest.raises(UIActionError, match="already resolved"):
        list(app.stream_ui_action(chat["chat_id"], message["message_id"], "reject", approval_id, {}, "approval-key-02"))


def test_invalid_approval_target_is_not_marked_approved(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)
    chat = app.create_chat("Analysis", ["analyst"], kind="direct")
    spec = {"schemaVersion": "1", "root": {"type": "ApprovalCard", "props": {
        "title": "Run", "action": {"type": "run_analysis", "agentIds": ["outside"], "objective": "Run"},
    }}}
    message = add_ui_message(app, chat, spec)
    approval_id = message["metadata"]["agent_ui"]["content"]["ui"]["root"]["props"]["approvalId"]
    with pytest.raises(UIActionError, match="outside this chat"):
        list(app.stream_ui_action(chat["chat_id"], message["message_id"], "approve", approval_id, {}, "approval-key-03"))
    saved = app.get_chat(chat["chat_id"])["messages"][-1]["metadata"]["agent_ui"]
    assert saved["content"]["approvals"][approval_id]["status"] == "pending"


def test_form_values_are_limited_to_declared_fields_and_bounded_strings():
    form = {"type": "Form", "props": {}, "children": [
        {"type": "TextInput", "props": {"name": "period"}},
        {"type": "Select", "props": {"name": "region"}},
    ]}
    assert validate_form_args(form, {"period": "1y", "region": "global"}) == {"period": "1y", "region": "global"}
    with pytest.raises(UIActionError, match="unknown field"):
        validate_form_args(form, {"shell": "rm -rf /"})
    with pytest.raises(UIActionError, match="short strings"):
        validate_form_args(form, {"period": "x" * 4001})
