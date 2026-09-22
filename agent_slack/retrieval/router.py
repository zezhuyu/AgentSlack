from __future__ import annotations

import re

SYMBOL_PATTERN = re.compile(r"\b[a-zA-Z_][a-zA-Z0-9]*(?:_[a-zA-Z0-9]+)+\b|\b[A-Z][a-zA-Z0-9]*[A-Z][a-zA-Z0-9]*\b")
FILENAME_PATTERN = re.compile(r"\b[\w.-]+\.[A-Za-z0-9]{1,6}\b")
QUOTE_PATTERN = re.compile(r'"[^"]+"|`[^`]+`')

CONCEPTUAL_HINTS = (
    "how does", "how do", "why", "explain", "what is the purpose",
    "overview", "architecture", "design", "concept", "workflow",
)


def route(query: str) -> str:
    """Return 'exact', 'semantic', or 'hybrid' using cheap deterministic signals."""
    text = query.strip()
    lowered = text.lower()
    has_exact_signal = bool(
        SYMBOL_PATTERN.search(text) or FILENAME_PATTERN.search(text) or QUOTE_PATTERN.search(text)
    )
    has_conceptual_signal = any(hint in lowered for hint in CONCEPTUAL_HINTS)

    if has_exact_signal and has_conceptual_signal:
        return "hybrid"
    if has_exact_signal:
        return "hybrid" if len(text.split()) > 6 else "exact"
    return "semantic"
