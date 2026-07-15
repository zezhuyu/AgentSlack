from __future__ import annotations

from pathlib import Path

from agent_slack.storage import AgentSlackStorage


def test_create_chat_and_append_message(tmp_path: Path) -> None:
    storage = AgentSlackStorage(tmp_path / "data")
    chat = storage.create_chat("Test Chat", ["coordinator", "reviewer"])
    assert chat["chat_id"]
    loaded = storage.get_chat(chat["chat_id"])
    assert loaded is not None
    storage.append_message(
        chat["chat_id"],
        {
            "author_type": "user",
            "author_id": "user",
            "author_label": "You",
            "body": "hello",
        },
    )
    loaded = storage.get_chat(chat["chat_id"])
    assert loaded is not None
    assert len(loaded["messages"]) == 1
    assert loaded["messages"][0]["body"] == "hello"


def test_write_memory_files(tmp_path: Path) -> None:
    storage = AgentSlackStorage(tmp_path / "data")
    storage.write_memory("coordinator", {"summary": "test"}, "# Memory\n")
    assert (tmp_path / "data" / "memories" / "coordinator.json").exists()
    assert (tmp_path / "data" / "memories" / "coordinator.md").exists()
