from __future__ import annotations

import json
import re
from typing import Any
from uuid import uuid4


_TAG_PATTERN = re.compile(
    r"<agent-slack-question>\s*(\{[\s\S]*?\})\s*</agent-slack-question>",
    flags=re.IGNORECASE,
)
_FENCE_PATTERN = re.compile(
    r"```agent-slack-question\s*(\{[\s\S]*?\})\s*```",
    flags=re.IGNORECASE,
)
_MAX_OPTIONS = 12
_MAX_PROMPT_LENGTH = 2_000
_MAX_LABEL_LENGTH = 160
_MAX_DESCRIPTION_LENGTH = 500


def extract_choice_interaction(
    value: str,
    *,
    strict: bool = False,
) -> tuple[str, dict[str, Any] | None]:
    """Extract one agent-authored choice block while retaining readable prose."""
    original = str(value or "").strip()
    match = _TAG_PATTERN.search(original) or _FENCE_PATTERN.search(original)
    if not match:
        return original, None
    try:
        payload = json.loads(match.group(1))
        interaction = _normalize_choice(payload)
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        if strict:
            raise ValueError(str(exc)) from exc
        return original, None

    body = (original[: match.start()] + original[match.end() :]).strip()
    if not body:
        body = "Your input is needed before I continue."
    return body, interaction


def _normalize_choice(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("Question payload must be a JSON object")
    prompt = str(payload.get("prompt") or "").strip()
    if not prompt:
        raise ValueError("Question prompt is required")
    if len(prompt) > _MAX_PROMPT_LENGTH:
        raise ValueError(f"Question prompt must be at most {_MAX_PROMPT_LENGTH} characters")

    raw_options = payload.get("options")
    if not isinstance(raw_options, list) or len(raw_options) < 2:
        raise ValueError("Choice questions require at least two options")
    if len(raw_options) > _MAX_OPTIONS:
        raise ValueError(f"Choice questions support at most {_MAX_OPTIONS} options")

    options: list[dict[str, str]] = []
    seen_ids: set[str] = set()
    for index, raw_option in enumerate(raw_options, start=1):
        if not isinstance(raw_option, dict):
            raise ValueError("Every choice option must be an object")
        option_id = str(raw_option.get("id") or f"option-{index}").strip()
        label = str(raw_option.get("label") or "").strip()
        description = str(raw_option.get("description") or "").strip()
        if not option_id or option_id in seen_ids:
            raise ValueError("Choice option ids must be non-empty and unique")
        if not label:
            raise ValueError("Every choice option requires a label")
        if len(label) > _MAX_LABEL_LENGTH:
            raise ValueError(f"Choice labels must be at most {_MAX_LABEL_LENGTH} characters")
        if len(description) > _MAX_DESCRIPTION_LENGTH:
            raise ValueError(
                f"Choice descriptions must be at most {_MAX_DESCRIPTION_LENGTH} characters"
            )
        seen_ids.add(option_id)
        option = {"id": option_id, "label": label}
        if description:
            option["description"] = description
        options.append(option)

    return {
        "kind": "choice",
        "question_id": uuid4().hex,
        "prompt": prompt,
        "options": options,
        "multiple": bool(payload.get("multiple", False)),
        "status": "pending",
        "selected_option_ids": [],
    }
