import json
import pytest

from agent_slack import runners


def test_codex_model_cache_filters_hidden_and_reads_default(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    (tmp_path / "models_cache.json").write_text(json.dumps({"models": [
        {"slug": "shown", "display_name": "Shown", "visibility": "list", "priority": 1},
        {"slug": "secret", "visibility": "hide", "priority": 0},
    ]}))
    (tmp_path / "config.toml").write_text('model = "shown"\n')
    models, default = runners._codex_models()
    assert [item["id"] for item in models] == ["shown"]
    assert default == "shown"


def test_claude_discovery_and_settings_default(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    project = tmp_path / "projects" / "project"
    project.mkdir(parents=True)
    (project / "session.jsonl").write_text('\n'.join([
        json.dumps({"message": {"model": "claude-sonnet-9-9"}}),
        json.dumps({"message": {"model": "<synthetic>"}}),
    ]))
    (tmp_path / "settings.json").write_text(json.dumps({"model": "sonnet"}))
    models, default = runners._claude_models()
    assert models[0]["id"] == "claude-sonnet-9-9"
    assert default == "sonnet"


def test_missing_codex_cache_is_safe(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    assert runners._codex_models() == ([], None)


def test_validate_choice_requires_available_runner_and_known_model(monkeypatch):
    monkeypatch.setattr(runners, "list_runners", lambda: {"runners": [
        {"id": "codex", "label": "Codex", "available": True, "models": [{"id": "gpt-x"}]},
        {"id": "claude", "label": "Claude Code", "available": False, "models": []},
    ]})
    assert runners.validate_choice("codex", "gpt-x") == ("codex", "gpt-x")
    assert runners.validate_choice("codex", None) == ("codex", None)
    try:
        runners.validate_choice("codex", "gpt-no")
    except ValueError:
        pass
    else:
        raise AssertionError("unknown model should be rejected")
    try:
        runners.validate_choice("claude", "sonnet")
    except ValueError:
        pass
    else:
        raise AssertionError("unavailable runner should be rejected")


def test_validate_choice_uses_safe_identifier_fallback_when_discovery_is_empty(monkeypatch):
    monkeypatch.setattr(runners, "list_runners", lambda: {"runners": [
        {"id": "codex", "label": "Codex", "available": True, "models": []},
        {"id": "claude", "label": "Claude Code", "available": True, "models": []},
    ]})
    assert runners.validate_choice("claude", "org/model@v1") == ("claude", "org/model@v1")
    with pytest.raises(ValueError):
        runners.validate_choice("claude", "bad model")
