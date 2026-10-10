"""Closed registry for actions that declarative Agent UI may request."""

from __future__ import annotations

import json
import re
from typing import Any

ACTION_TYPES = {"run_analysis", "submit_form", "answer_question", "approve", "reject"}
MAX_ACTION_ARGS_BYTES = 16 * 1024
_FIELD_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")


class UIActionError(ValueError):
    pass


def find_action_node(root: dict[str, Any], action_type: str, node_id: str = "") -> dict[str, Any] | None:
    """Find an action capability in the saved spec; reject client-invented verbs."""
    if action_type not in ACTION_TYPES:
        return None
    found = None

    def visit(node: dict[str, Any]) -> None:
        nonlocal found
        if found is not None:
            return
        props = node.get("props") or {}
        if action_type in {"approve", "reject"}:
            matches = node.get("type") == "ApprovalCard" and str(props.get("approvalId") or "") == node_id
        else:
            matches = (
                str(props.get("id") or "") == node_id
                if node_id
                else (props.get("action") or {}).get("type") == action_type
            )
        if matches and ((props.get("action") or {}).get("type") == action_type or action_type in {"approve", "reject"}):
            found = node
            return
        for child in node.get("children") or []:
            visit(child)

    visit(root)
    return found


def validate_form_args(node: dict[str, Any], args: Any) -> dict[str, str]:
    if not isinstance(args, dict):
        raise UIActionError("Form values must be an object")
    fields: set[str] = set()

    def collect(item: dict[str, Any]) -> None:
        if item.get("type") in {"TextInput", "Select", "Checkbox", "Slider"}:
            name = str((item.get("props") or {}).get("name") or "")
            if _FIELD_NAME.fullmatch(name):
                fields.add(name)
        for child in item.get("children") or []:
            collect(child)

    collect(node)
    if not fields:
        raise UIActionError("This form has no valid fields")
    if set(args) - fields:
        raise UIActionError("Form submitted an unknown field")
    result: dict[str, str] = {}
    for key, value in args.items():
        if not isinstance(value, str) or len(value) > 4_000:
            raise UIActionError("Form values must be short strings")
        result[key] = value
    if len(json.dumps(result, ensure_ascii=False).encode("utf-8")) > MAX_ACTION_ARGS_BYTES:
        raise UIActionError("Form submission is too large")
    return result
