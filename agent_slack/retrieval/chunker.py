from __future__ import annotations

import re
from dataclasses import dataclass

CODE_WINDOW_LINES = 60
CODE_WINDOW_OVERLAP = 10
TEXT_WINDOW_LINES = 40

CODE_EXTENSIONS = {
    ".py", ".js", ".ts", ".tsx", ".jsx", ".swift", ".rs", ".go", ".java", ".kt",
    ".c", ".cpp", ".h", ".hpp", ".sh", ".sql",
}
MARKDOWN_EXTENSIONS = {".md", ".rst"}
CONFIG_EXTENSIONS = {".json", ".yaml", ".yml", ".toml", ".html", ".css"}

SYMBOL_PATTERN = re.compile(
    r"^\s*(?:def|class|func|function|fn|struct|interface|impl|const|let|var)\s+[A-Za-z_][A-Za-z0-9_]*"
)


@dataclass
class Chunk:
    start_line: int
    end_line: int
    content: str


def chunk_file(relative_path: str, text: str) -> list[Chunk]:
    suffix = "." + relative_path.rsplit(".", 1)[-1].lower() if "." in relative_path else ""
    lines = text.splitlines()
    if not lines:
        return []
    if suffix in MARKDOWN_EXTENSIONS:
        return _chunk_markdown(lines)
    if suffix in CODE_EXTENSIONS:
        return _chunk_symbol_aware(lines)
    return _chunk_window(lines, TEXT_WINDOW_LINES, overlap=5)


def _chunk_markdown(lines: list[str]) -> list[Chunk]:
    sections: list[tuple[int, int]] = []
    heading_starts = [index for index, line in enumerate(lines) if line.lstrip().startswith("#")]
    if not heading_starts or heading_starts[0] != 0:
        heading_starts = [0] + heading_starts
    for index, start in enumerate(heading_starts):
        end = heading_starts[index + 1] if index + 1 < len(heading_starts) else len(lines)
        if end > start:
            sections.append((start, end))
    chunks = [
        Chunk(start_line=start + 1, end_line=end, content="\n".join(lines[start:end]))
        for start, end in sections
        if "\n".join(lines[start:end]).strip()
    ]
    return chunks or _chunk_window(lines, TEXT_WINDOW_LINES, overlap=5)


def _chunk_symbol_aware(lines: list[str]) -> list[Chunk]:
    boundaries = [index for index, line in enumerate(lines) if SYMBOL_PATTERN.match(line)]
    if len(boundaries) < 2:
        return _chunk_window(lines, CODE_WINDOW_LINES, overlap=CODE_WINDOW_OVERLAP)
    chunks: list[Chunk] = []
    for index, start in enumerate(boundaries):
        end = boundaries[index + 1] if index + 1 < len(boundaries) else len(lines)
        content = "\n".join(lines[start:end])
        if content.strip():
            chunks.append(Chunk(start_line=start + 1, end_line=end, content=content))
    if boundaries[0] > 0:
        header = "\n".join(lines[: boundaries[0]])
        if header.strip():
            chunks.insert(0, Chunk(start_line=1, end_line=boundaries[0], content=header))
    return chunks


def _chunk_window(lines: list[str], size: int, overlap: int) -> list[Chunk]:
    chunks: list[Chunk] = []
    step = max(size - overlap, 1)
    index = 0
    while index < len(lines):
        end = min(index + size, len(lines))
        content = "\n".join(lines[index:end])
        if content.strip():
            chunks.append(Chunk(start_line=index + 1, end_line=end, content=content))
        if end >= len(lines):
            break
        index += step
    return chunks
