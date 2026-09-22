from __future__ import annotations

import math
import re
from collections import Counter

TOKEN_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_]{1,40}")


def tokenize(text: str) -> list[str]:
    """Split identifiers on case/underscore boundaries so 'grepSearch' matches 'grep search'."""
    tokens: list[str] = []
    for raw in TOKEN_PATTERN.findall(text.lower()):
        tokens.append(raw)
        parts = re.split(r"_+", raw)
        if len(parts) > 1:
            tokens.extend(part for part in parts if len(part) > 1)
        camel_parts = re.findall(r"[a-z0-9]+", re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", raw))
        if len(camel_parts) > 1:
            tokens.extend(part.lower() for part in camel_parts if len(part) > 1)
    return tokens


class LocalEmbeddingIndex:
    """A dependency-free stand-in for a neural embedding model.

    Chunks are represented as TF-IDF weighted term vectors; similarity is cosine
    distance over the shared vocabulary. This keeps "local RAG" fully offline with
    no model download, at the cost of true semantic (non-lexical) matching.
    """

    def __init__(self, document_frequency: dict[str, int], document_count: int) -> None:
        self.document_frequency = document_frequency
        self.document_count = max(document_count, 1)

    @classmethod
    def build(cls, documents: list[str]) -> "LocalEmbeddingIndex":
        document_frequency: Counter[str] = Counter()
        for document in documents:
            document_frequency.update(set(tokenize(document)))
        return cls(dict(document_frequency), len(documents))

    def idf(self, term: str) -> float:
        frequency = self.document_frequency.get(term, 0)
        return math.log((self.document_count + 1) / (frequency + 1)) + 1.0

    def vectorize(self, text: str) -> dict[str, float]:
        counts = Counter(tokenize(text))
        if not counts:
            return {}
        vector = {term: count * self.idf(term) for term, count in counts.items()}
        norm = math.sqrt(sum(value * value for value in vector.values())) or 1.0
        return {term: value / norm for term, value in vector.items()}


def cosine_similarity(a: dict[str, float], b: dict[str, float]) -> float:
    if not a or not b:
        return 0.0
    shorter, longer = (a, b) if len(a) < len(b) else (b, a)
    return sum(value * longer.get(term, 0.0) for term, value in shorter.items())
