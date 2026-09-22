from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass

from .embeddings import LocalEmbeddingIndex, cosine_similarity


@dataclass
class SemanticResult:
    path: str
    start_line: int
    end_line: int
    content: str
    score: float


def semantic_search(connection: sqlite3.Connection, query: str, top_k: int = 12) -> list[SemanticResult]:
    document_count_row = connection.execute("SELECT COUNT(*) FROM chunks").fetchone()
    document_count = document_count_row[0] if document_count_row else 0
    if not document_count:
        return []
    frequency = dict(connection.execute("SELECT term, doc_count FROM term_document_frequency").fetchall())
    index = LocalEmbeddingIndex(frequency, document_count)
    query_vector = index.vectorize(query)
    if not query_vector:
        return []

    results: list[SemanticResult] = []
    rows = connection.execute(
        "SELECT chunks.start_line, chunks.end_line, chunks.content, chunks.token_counts, files.relative_path "
        "FROM chunks JOIN files ON files.id = chunks.file_id"
    )
    for start_line, end_line, content, token_json, relative_path in rows:
        counts = json.loads(token_json)
        vector = {}
        norm_terms = {term: count * index.idf(term) for term, count in counts.items()}
        norm = sum(value * value for value in norm_terms.values()) ** 0.5 or 1.0
        vector = {term: value / norm for term, value in norm_terms.items()}
        score = cosine_similarity(query_vector, vector)
        if score <= 0:
            continue
        results.append(SemanticResult(
            path=relative_path, start_line=start_line, end_line=end_line, content=content, score=score,
        ))
    results.sort(key=lambda item: item.score, reverse=True)
    return results[:top_k]
