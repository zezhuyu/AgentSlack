from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from .embeddings import tokenize
from .grep import GrepUnavailable, grep_search
from .indexer import build_index, index_status, open_index, update_file
from .paths import resolve_within
from .rag import semantic_search
from .reranker import RetrievedChunk, merge_and_rerank
from .router import route

MAX_READ_LINES = 500
MAX_READ_BYTES = 200_000
MAX_RETRIEVAL_ROUNDS = 4


@dataclass
class RetrievalContext:
    strategy: str
    chunks: list[RetrievedChunk]

    def as_prompt_block(self) -> str:
        if not self.chunks:
            return ""
        parts = ["Retrieved local context:"]
        for chunk in self.chunks:
            parts.append(f"\n### {chunk.path}:{chunk.start_line}-{chunk.end_line}\n{chunk.content}")
        return "\n".join(parts)

    def citations(self) -> list[dict[str, object]]:
        return [
            {"path": chunk.path, "start_line": chunk.start_line, "end_line": chunk.end_line}
            for chunk in self.chunks
        ]


class RetrievalManager:
    """Read-only local retrieval: exact grep, local semantic search, and hybrid merge.

    This is the sole capability boundary Chat mode exposes to a model: no shell,
    no write access, no execution. Everything here is scoped to one server's
    project_root and validated through resolve_within.
    """

    def __init__(self, project_root: Path, index_db_path: Path) -> None:
        self.project_root = project_root
        self.index_db_path = index_db_path

    def _connection(self) -> sqlite3.Connection:
        return open_index(self.index_db_path)

    def ensure_index(self) -> int:
        connection = self._connection()
        try:
            return build_index(connection, self.project_root)
        finally:
            connection.close()

    def refresh_file(self, path: Path) -> None:
        connection = self._connection()
        try:
            update_file(connection, self.project_root, path)
            connection.commit()
        finally:
            connection.close()

    def status(self) -> dict[str, str]:
        connection = self._connection()
        try:
            return index_status(connection)
        finally:
            connection.close()

    def search(self, query: str, top_k: int = 8) -> RetrievalContext:
        strategy = route(query)
        grep_results = []
        semantic_results = []
        try:
            if strategy in ("exact", "hybrid"):
                grep_results = grep_search(query, self.project_root)
        except GrepUnavailable:
            grep_results = []
        if strategy in ("semantic", "hybrid"):
            connection = self._connection()
            try:
                semantic_results = semantic_search(connection, query, top_k=max(top_k * 2, 12))
            finally:
                connection.close()
        query_terms = set(tokenize(query))
        chunks = merge_and_rerank(grep_results, semantic_results, query_terms, top_k=top_k)
        return RetrievalContext(strategy=strategy, chunks=chunks)

    def read_file(self, raw_path: str, start_line: int | None = None, end_line: int | None = None) -> dict[str, object] | None:
        path = resolve_within(self.project_root, raw_path)
        if path is None:
            return None
        try:
            if path.stat().st_size > MAX_READ_BYTES and start_line is None:
                return {"path": raw_path, "error": f"File exceeds {MAX_READ_BYTES} bytes; request a line range"}
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return None
        lines = text.splitlines()
        begin = max((start_line or 1) - 1, 0)
        finish = min(end_line or len(lines), begin + MAX_READ_LINES, len(lines))
        relative = path.relative_to(self.project_root.resolve())
        return {
            "path": str(relative),
            "start_line": begin + 1,
            "end_line": finish,
            "content": "\n".join(lines[begin:finish]),
        }

    def list_directory(self, raw_path: str = ".") -> list[dict[str, object]] | None:
        root = self.project_root.resolve()
        candidate = (root / raw_path).resolve() if raw_path not in ("", ".") else root
        if not candidate.is_relative_to(root) or not candidate.is_dir():
            return None
        entries = []
        for child in sorted(candidate.iterdir(), key=lambda item: item.name):
            if child.name.startswith("."):
                continue
            entries.append({
                "name": child.name,
                "is_directory": child.is_dir(),
                "path": str(child.relative_to(root)),
            })
        return entries
