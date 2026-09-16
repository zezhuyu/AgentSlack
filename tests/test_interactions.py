from __future__ import annotations

import pytest

from agent_slack.interactions import extract_choice_interaction


def test_extracts_agent_supplied_choice_question_and_removes_transport_markup() -> None:
    body, interaction = extract_choice_interaction(
        """
I finished the specification and need your approval before implementation.

<agent-slack-question>
{
  "prompt": "Approve the specification?",
  "multiple": false,
  "options": [
    {"id": "approve", "label": "Approve", "description": "Begin implementation."},
    {"id": "revise", "label": "Request changes", "description": "Return to specification work."}
  ]
}
</agent-slack-question>
"""
    )

    assert body == "I finished the specification and need your approval before implementation."
    assert interaction is not None
    assert interaction["kind"] == "choice"
    assert interaction["status"] == "pending"
    assert interaction["prompt"] == "Approve the specification?"
    assert interaction["multiple"] is False
    assert interaction["options"] == [
        {"id": "approve", "label": "Approve", "description": "Begin implementation."},
        {"id": "revise", "label": "Request changes", "description": "Return to specification work."},
    ]
    assert interaction["selected_option_ids"] == []


def test_rejects_invalid_or_empty_dynamic_choice_payloads() -> None:
    invalid = """
<agent-slack-question>
{"prompt":"Choose","options":[{"id":"same","label":"One"},{"id":"same","label":"Two"}]}
</agent-slack-question>
"""
    assert extract_choice_interaction(invalid) == (invalid.strip(), None)

    empty = """
<agent-slack-question>
{"prompt":"Choose","options":[]}
</agent-slack-question>
"""
    assert extract_choice_interaction(empty) == (empty.strip(), None)


def test_limits_choice_questions_to_safe_payload_sizes() -> None:
    options = ",".join(
        f'{{"id":"option-{index}","label":"Option {index}"}}'
        for index in range(13)
    )
    body = (
        "<agent-slack-question>"
        f'{{"prompt":"Choose","options":[{options}]}}'
        "</agent-slack-question>"
    )

    with pytest.raises(ValueError, match="at most 12"):
        extract_choice_interaction(body, strict=True)
