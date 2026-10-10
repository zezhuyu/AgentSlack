"""Validation and extraction for the declarative Agent UI message format."""

from __future__ import annotations

import json
import re
from typing import Any
from uuid import uuid4

SCHEMA_VERSION = "1"
MAX_SPEC_BYTES = 64 * 1024
MAX_NODES = 120
MAX_DEPTH = 8
MAX_TEXT = 4_000
ACTION_TYPES = {"run_analysis", "submit_form", "answer_question", "approve", "reject"}
PROP_KEYS = {
    "Stack": {"gap"}, "Row": {"gap"}, "Grid": {"columns"},
    "Card": {"title"}, "Divider": set(), "Tabs": {"tabs"},
    "Text": {"text"}, "Heading": {"text", "level"}, "Badge": {"label", "status"},
    "Spinner": {"label"},
    "Progress": {"label", "value"}, "Statistic": {"label", "value", "change"},
    "Table": {"columns", "rows"}, "LineChart": {"title", "data", "animation"},
    "BarChart": {"title", "data", "animation"}, "PieChart": {"title", "data", "animation"},
    "Button": {"id", "label", "action"}, "TextInput": {"label", "name", "placeholder", "value", "maxLength"},
    "Select": {"label", "name", "options", "value"}, "Checkbox": {"label", "name", "checked"},
    "Slider": {"label", "min", "max", "value", "name"},
    "Form": {"id", "action", "submitLabel"}, "AgentStatus": {"label", "name", "status", "progress", "summary"},
    "TaskProgress": {"label", "name", "title", "status", "progress", "summary"},
    "ToolResult": {"title", "tool", "result", "summary"},
    "ApprovalCard": {"title", "summary", "impact", "action", "approvalId", "status"},
    "ArtifactPreview": {"title", "filename", "summary", "artifactId"},
}
_BLOCK = re.compile(r"```agent-ui\s*\n(.*?)\n```", re.DOTALL | re.IGNORECASE)


class AgentUISpecError(ValueError):
    pass


def validate_spec(value: Any) -> dict[str, Any]:
    """Validate a JSON-only tree; never interpret strings as code or markup."""
    try:
        encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise AgentUISpecError("UI must be JSON serializable") from exc
    if len(encoded.encode("utf-8")) > MAX_SPEC_BYTES:
        raise AgentUISpecError("UI specification exceeds 64 KiB")
    if not isinstance(value, dict) or value.get("schemaVersion") != SCHEMA_VERSION:
        raise AgentUISpecError("Unsupported UI schema version")
    root = value.get("root")
    count = 0

    def visit(node: Any, depth: int) -> None:
        nonlocal count
        count += 1
        if count > MAX_NODES:
            raise AgentUISpecError("UI specification has too many components")
        if depth > MAX_DEPTH:
            raise AgentUISpecError("UI specification is nested too deeply")
        if not isinstance(node, dict) or not isinstance(node.get("type"), str):
            raise AgentUISpecError("Each UI node requires a component type")
        props = node.get("props", {})
        children = node.get("children", [])
        if not isinstance(props, dict) or not isinstance(children, list):
            raise AgentUISpecError("Component props and children have invalid types")
        if any(not isinstance(key, str) for key in props):
            raise AgentUISpecError("Component prop names must be strings")
        accepted = PROP_KEYS.get(node["type"])
        if accepted is not None and set(props) - accepted:
            raise AgentUISpecError("Component contains unsupported properties")
        animation = props.get("animation")
        expected_animation = {"LineChart": "draw", "BarChart": "grow", "PieChart": "reveal"}.get(node["type"])
        if animation is not None and animation not in {"none", expected_animation}:
            raise AgentUISpecError("Unsupported chart animation")
        for key in ("text", "label", "title", "summary", "impact", "change", "placeholder"):
            if key in props and (not isinstance(props[key], str) or len(props[key]) > MAX_TEXT):
                raise AgentUISpecError(f"{key} must be a string of at most {MAX_TEXT} characters")
        if "action" in props:
            action = props["action"]
            if not isinstance(action, dict) or action.get("type") not in ACTION_TYPES:
                raise AgentUISpecError("Unsupported UI action")
            if set(action) - {"type", "agentIds", "objective", "questionId", "optionIds"}:
                raise AgentUISpecError("UI action contains unsupported properties")
            if "objective" in action and (not isinstance(action["objective"], str) or len(action["objective"]) > MAX_TEXT):
                raise AgentUISpecError("UI action objective is invalid")
        for child in children:
            visit(child, depth + 1)

    visit(root, 1)
    return value


def extract_agent_ui(body: str) -> tuple[str, dict[str, Any] | None, str | None]:
    """Remove and validate one explicit UI block; malformed blocks become plain text."""
    match = _BLOCK.search(body or "")
    if not match:
        return body, None, None
    text = (body[:match.start()] + body[match.end():]).strip()
    try:
        if len(match.group(1).encode("utf-8")) > MAX_SPEC_BYTES:
            raise AgentUISpecError("UI specification exceeds 64 KiB")
        spec = validate_spec(json.loads(match.group(1)))
    except (json.JSONDecodeError, AgentUISpecError) as exc:
        return text, None, str(exc)
    return text, spec, None


def bind_ui_capabilities(spec: dict[str, Any]) -> dict[str, Any]:
    """Bind actionable nodes to opaque IDs and create server-owned approval records."""
    approvals: dict[str, dict[str, Any]] = {}

    def visit(node: dict[str, Any]) -> None:
        props = node.get("props") or {}
        if node.get("type") in {"Button", "Form"} and isinstance(props.get("action"), dict):
            props["id"] = uuid4().hex
        if node.get("type") == "ApprovalCard":
            action = props.get("action") or {}
            if action.get("type") != "run_analysis":
                raise AgentUISpecError("Approval cards may only approve a bounded agent run")
            approval_id = uuid4().hex
            props["approvalId"] = approval_id
            approvals[approval_id] = {"status": "pending", "action": action}
        for child in node.get("children") or []:
            visit(child)

    visit(spec["root"])
    return approvals
