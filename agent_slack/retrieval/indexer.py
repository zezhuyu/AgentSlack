from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from collections import Counter
from pathlib import Path

from .chunker import chunk_file
from .embeddings import tokenize
from .grep import IGNORED_DIRECTORIES

INDEXABLE_EXTENSIONS = {
    ".py", ".js", ".ts", ".tsx", ".jsx", ".swift", ".rs", ".go", ".java", ".kt",
    ".c", ".cpp", ".h", ".hpp",
    ".md", ".txt", ".rst",
    ".json", ".yaml", ".yml", ".toml",
    ".html", ".css", ".sql", ".sh",
}
MAX_FILE_BYTES = 1_000_000

SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    id INTEGER PRIMARY KEY,
    relative_path TEXT UNIQUE NOT NULL,
    content_hash TEXT NOT NULL,
    modified_at REAL NOT NULL,
    indexed_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS chunks (
    id INTEGER PRIMARY KEY,
    file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    start_line INTEGER NOT NULL,
    end_line INTEGER NOT NULL,
    content TEXT NOT NULL,
    token_counts TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS term_document_frequency (
    term TEXT PRIMARY KEY,
    doc_count INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS index_metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chunks_file_id ON chunks(file_id);
"""


def open_index(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(db_path))
    connection.execute("PRAGMA journal_mode=WAL")
    connection.executescript(SCHEMA)
    return connection


def _hash_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def discover_files(project_root: Path) -> list[Path]:
    results: list[Path] = []
    for path in project_root.rglob("*"):
        if not path.is_file():
            continue
        if any(part in IGNORED_DIRECTORIES or part.startswith(".") for part in path.relative_to(project_root).parts[:-1]):
            continue
        if path.suffix.lower() not in INDEXABLE_EXTENSIONS:
            continue
        try:
            if path.stat().st_size > MAX_FILE_BYTES:
                continue
        except OSError:
            continue
        results.append(path)
    return results


def _adjust_term_frequency(connection: sqlite3.Connection, terms: set[str], delta: int) -> None:
    for term in terms:
        row = connection.execute(
            "SELECT doc_count FROM term_document_frequency WHERE term = ?", (term,)
        ).fetchone()
        current = row[0] if row else 0
        updated = current + delta
        if updated <= 0:
            connection.execute("DELETE FROM term_document_frequency WHERE term = ?", (term,))
        else:
            connection.execute(
                "INSERT INTO term_document_frequency(term, doc_count) VALUES (?, ?) "
                "ON CONFLICT(term) DO UPDATE SET doc_count = excluded.doc_count",
                (term, updated),
            )


def remove_file(connection: sqlite3.Connection, relative_path: str) -> None:
    row = connection.execute("SELECT id FROM files WHERE relative_path = ?", (relative_path,)).fetchone()
    if not row:
        return
    file_id = row[0]
    chunk_rows = connection.execute("SELECT token_counts FROM chunks WHERE file_id = ?", (file_id,)).fetchall()
    for (token_json,) in chunk_rows:
        _adjust_term_frequency(connection, set(json.loads(token_json)), -1)
    connection.execute("DELETE FROM chunks WHERE file_id = ?", (file_id,))
    connection.execute("DELETE FROM files WHERE id = ?", (file_id,))


def update_file(connection: sqlite3.Connection, project_root: Path, path: Path) -> None:
    relative_path = str(path.relative_to(project_root))
    try:
        content = path.read_bytes()
    except OSError:
        return
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        return
    content_hash = _hash_bytes(content)
    existing = connection.execute(
        "SELECT content_hash FROM files WHERE relative_path = ?", (relative_path,)
    ).fetchone()
    if existing and existing[0] == content_hash:
        return
    remove_file(connection, relative_path)
    cursor = connection.execute(
        "INSERT INTO files(relative_path, content_hash, modified_at, indexed_at) VALUES (?, ?, ?, ?)",
        (relative_path, content_hash, path.stat().st_mtime, time.time()),
    )
    file_id = cursor.lastrowid
    for chunk in chunk_file(relative_path, text):
        counts = Counter(tokenize(chunk.content))
        if not counts:
            continue
        connection.execute(
            "INSERT INTO chunks(file_id, start_line, end_line, content, token_counts) VALUES (?, ?, ?, ?, ?)",
            (file_id, chunk.start_line, chunk.end_line, chunk.content, json.dumps(dict(counts))),
        )
        _adjust_term_frequency(connection, set(counts), 1)


def build_index(connection: sqlite3.Connection, project_root: Path) -> int:
    files = discover_files(project_root)
    seen = set()
    for path in files:
        update_file(connection, project_root, path)
        seen.add(str(path.relative_to(project_root)))
    existing_paths = {row[0] for row in connection.execute("SELECT relative_path FROM files")}
    for stale in existing_paths - seen:
        remove_file(connection, stale)
    connection.execute(
        "INSERT INTO index_metadata(key, value) VALUES ('status', 'ready') "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value"
    )
    connection.execute(
        "INSERT INTO index_metadata(key, value) VALUES ('indexed_at', ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (str(time.time()),),
    )
    connection.commit()
    return len(files)


def index_status(connection: sqlite3.Connection) -> dict[str, str]:
    rows = connection.execute("SELECT key, value FROM index_metadata").fetchall()
    return {key: value for key, value in rows} or {"status": "not_indexed"}
