import json

import pytest

from agent_slack.generative_ui import AgentUISpecError, extract_agent_ui, validate_spec


def test_extracts_valid_ui_block_and_preserves_surrounding_markdown():
    body = "Summary first.\n\n```agent-ui\n" + json.dumps({
        "schemaVersion": "1",
        "root": {"type": "Card", "props": {}, "children": [
            {"type": "Heading", "props": {"text": "Portfolio"}},
        ]},
    }) + "\n```\n\nSummary after."

    text, spec, error = extract_agent_ui(body)

    assert text == "Summary first.\n\n\n\nSummary after."
    assert spec["schemaVersion"] == "1"
    assert error is None


@pytest.mark.parametrize("spec", [
    None,
    {"schemaVersion": "99", "root": {"type": "Card"}},
    {"schemaVersion": "1", "root": {"props": {}}},
    {"schemaVersion": "1", "root": {"type": "Card", "props": {}, "children": {}}},
])
def test_rejects_invalid_spec(spec):
    with pytest.raises(AgentUISpecError):
        validate_spec(spec)


def test_rejects_unsupported_component_properties_and_action_types():
    with pytest.raises(AgentUISpecError, match="unsupported properties"):
        validate_spec({"schemaVersion": "1", "root": {
            "type": "Button", "props": {"label": "Run", "onclick": "danger"},
        }})
    with pytest.raises(AgentUISpecError, match="Unsupported UI action"):
        validate_spec({"schemaVersion": "1", "root": {
            "type": "Button", "props": {"label": "Run", "action": {"type": "shell"}},
        }})


def test_spinner_is_a_registered_accessible_animation_component():
    assert validate_spec({"schemaVersion": "1", "root": {
        "type": "Spinner", "props": {"label": "Loading report"},
    }})["root"]["type"] == "Spinner"
    with pytest.raises(AgentUISpecError, match="unsupported properties"):
        validate_spec({"schemaVersion": "1", "root": {
            "type": "Spinner", "props": {"label": "Loading", "duration": "arbitrary"},
        }})


def test_chart_animation_is_a_small_allowlisted_choice():
    assert validate_spec({"schemaVersion": "1", "root": {
        "type": "LineChart", "props": {"title": "Demo", "data": [], "animation": "draw"},
    }})["root"]["props"]["animation"] == "draw"
    with pytest.raises(AgentUISpecError, match="unsupported properties"):
        validate_spec({"schemaVersion": "1", "root": {"type": "Text", "props": {"animation": "draw"}}})
    for node_type, animation in (("LineChart", "grow"), ("PieChart", "javascript:evil()")):
        with pytest.raises(AgentUISpecError, match="chart animation"):
            validate_spec({"schemaVersion": "1", "root": {
                "type": node_type, "props": {"animation": animation},
            }})


def test_rejects_oversized_and_deep_specs():
    large = {"schemaVersion": "1", "root": {"type": "Text", "props": {"text": "x" * 70000}}}
    with pytest.raises(AgentUISpecError, match="64 KiB"):
        validate_spec(large)

    node = {"type": "Text", "props": {}}
    for _ in range(9):
        node = {"type": "Stack", "props": {}, "children": [node]}
    with pytest.raises(AgentUISpecError, match="nested"):
        validate_spec({"schemaVersion": "1", "root": node})


def test_malformed_json_is_removed_and_falls_back_to_markdown():
    text, spec, error = extract_agent_ui("Before\n```agent-ui\n{broken}\n```\nAfter")
    assert text == "Before\n\nAfter"
    assert spec is None
    assert error


def test_oversized_fenced_payload_is_rejected_before_json_parsing():
    text, spec, error = extract_agent_ui("```agent-ui\n" + (" " * 70000) + "\n```")
    assert text == ""
    assert spec is None
    assert "64 KiB" in error


def test_ordinary_markdown_is_unchanged():
    body = "**Plain response**"
    assert extract_agent_ui(body) == (body, None, None)
