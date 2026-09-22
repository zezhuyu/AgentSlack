from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class RetrievedChunk:
    path: str
    start_line: int
    end_line: int
    content: str
    score: float
    sources: list[str] = field(default_factory=list)


def merge_and_rerank(
    grep_results: list,
    semantic_results: list,
    query_terms: set[str],
    top_k: int = 8,
) -> list[RetrievedChunk]:
    by_key: dict[tuple[str, int, int], RetrievedChunk] = {}

    for result in semantic_results:
        key = (result.path, result.start_line, result.end_line)
        by_key[key] = RetrievedChunk(
            path=result.path, start_line=result.start_line, end_line=result.end_line,
            content=result.content, score=result.score, sources=["semantic"],
        )

    for result in grep_results:
        matched_key = next(
            (key for key in by_key if key[0] == result.path and key[1] <= result.line <= key[2]),
            None,
        )
        exact_bonus = 0.5
        path_bonus = 0.1 if any(term in result.path.lower() for term in query_terms) else 0.0
        if matched_key:
            chunk = by_key[matched_key]
            chunk.score += exact_bonus + path_bonus
            if "exact" not in chunk.sources:
                chunk.sources.append("exact")
        else:
            key = (result.path, result.line, result.line)
            by_key[key] = RetrievedChunk(
                path=result.path, start_line=result.line, end_line=result.line,
                content=result.text, score=exact_bonus + result.score * 0.1 + path_bonus,
                sources=["exact"],
            )

    ranked = sorted(by_key.values(), key=lambda chunk: chunk.score, reverse=True)
    return ranked[:top_k]
