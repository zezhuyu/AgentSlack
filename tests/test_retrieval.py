from __future__ import annotations

from pathlib import Path

import pytest

from agent_slack.retrieval.chunker import chunk_file
from agent_slack.retrieval.embeddings import LocalEmbeddingIndex, cosine_similarity, tokenize
from agent_slack.retrieval.grep import grep_search
from agent_slack.retrieval.indexer import build_index, open_index, update_file
from agent_slack.retrieval.manager import RetrievalManager
from agent_slack.retrieval.paths import resolve_within
from agent_slack.retrieval.rag import semantic_search
from agent_slack.retrieval.reranker import merge_and_rerank
from agent_slack.retrieval.router import route


def _write(root: Path, relative: str, content: str) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


# --- grep ---------------------------------------------------------------

def test_grep_finds_exact_symbol(tmp_path: Path) -> None:
    _write(tmp_path, "orchestrator.py", "def stream_agent_reply():\n    pass\n")
    results = grep_search("stream_agent_reply", tmp_path)
    assert any(result.path == "orchestrator.py" and result.line == 1 for result in results)


def test_grep_is_case_insensitive_by_default(tmp_path: Path) -> None:
    _write(tmp_path, "notes.txt", "Hello World\n")
    assert grep_search("hello world", tmp_path)


def test_grep_handles_special_characters_without_shell_injection(tmp_path: Path) -> None:
    _write(tmp_path, "notes.txt", "cost is $5.00 (approx)\n")
    results = grep_search("$5.00 (approx)", tmp_path)
    assert any("5.00" in result.text for result in results)


def test_grep_ignores_dot_and_build_directories(tmp_path: Path) -> None:
    _write(tmp_path, "node_modules/pkg/index.js", "needle_token\n")
    _write(tmp_path, ".git/objects/needle_token", "needle_token\n")
    _write(tmp_path, "src/app.js", "needle_token\n")
    results = grep_search("needle_token", tmp_path)
    paths = {result.path for result in results}
    assert paths == {"src/app.js"}


def test_grep_returns_empty_for_blank_query(tmp_path: Path) -> None:
    assert grep_search("   ", tmp_path) == []


# --- path safety ----------------------------------------------------------

def test_resolve_within_accepts_relative_and_absolute_paths(tmp_path: Path) -> None:
    target = _write(tmp_path, "notes/report.md", "hi")
    assert resolve_within(tmp_path, "notes/report.md") == target
    assert resolve_within(tmp_path, str(target)) == target
    assert resolve_within(tmp_path, f"{target}:42") == target


def test_resolve_within_rejects_traversal_and_missing_files(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside.md"
    outside.write_text("nope", encoding="utf-8")
    try:
        assert resolve_within(tmp_path, "../outside.md") is None
        assert resolve_within(tmp_path, str(outside)) is None
    finally:
        outside.unlink()
    assert resolve_within(tmp_path, "does/not/exist.md") is None


def test_resolve_within_rejects_secret_looking_files(tmp_path: Path) -> None:
    _write(tmp_path, "credentials.pem", "secret")
    assert resolve_within(tmp_path, "credentials.pem") is None


def test_resolve_within_allows_ordinary_content_in_a_dot_prefixed_directory(tmp_path: Path) -> None:
    # ".assets/" (and similar) is a common convention for generated/content
    # directories, not a secret - only VCS/credential directories and dotfile
    # *leaves* should be excluded, not every dot-prefixed ancestor.
    target = _write(tmp_path, ".assets/trials/character/contact-sheet.png", "binary-ish")
    assert resolve_within(tmp_path, ".assets/trials/character/contact-sheet.png") == target


def test_resolve_within_still_rejects_vcs_and_credential_directories(tmp_path: Path) -> None:
    _write(tmp_path, ".git/config", "junk")
    _write(tmp_path, ".ssh/id_rsa", "key")
    assert resolve_within(tmp_path, ".git/config") is None
    assert resolve_within(tmp_path, ".ssh/id_rsa") is None


def test_resolve_within_still_rejects_leaf_dotfiles(tmp_path: Path) -> None:
    _write(tmp_path, ".env", "SECRET=1")
    assert resolve_within(tmp_path, ".env") is None


def test_resolve_within_rejects_symlink_escape(tmp_path: Path) -> None:
    outside_dir = tmp_path.parent / "outside_dir"
    outside_dir.mkdir(exist_ok=True)
    secret = outside_dir / "secret.md"
    secret.write_text("nope", encoding="utf-8")
    link = tmp_path / "escape.md"
    try:
        link.symlink_to(secret)
        assert resolve_within(tmp_path, "escape.md") is None
    finally:
        link.unlink(missing_ok=True)
        secret.unlink(missing_ok=True)
        outside_dir.rmdir()


# --- chunker / embeddings --------------------------------------------------

def test_chunk_file_preserves_line_numbers_for_code() -> None:
    text = "def a():\n    pass\n\ndef b():\n    pass\n"
    chunks = chunk_file("app.py", text)
    assert chunks[0].start_line == 1
    assert "def a" in chunks[0].content


def test_chunk_file_is_heading_aware_for_markdown() -> None:
    text = "# Title\n\nintro\n\n## Section\n\nbody\n"
    chunks = chunk_file("doc.md", text)
    assert any(chunk.content.startswith("## Section") for chunk in chunks)


def test_tokenize_splits_snake_and_camel_case() -> None:
    tokens = tokenize("grepSearch and grep_search")
    assert "grep" in tokens and "search" in tokens


def test_local_embedding_cosine_similarity_prefers_matching_terms() -> None:
    index = LocalEmbeddingIndex.build(["retrieval manager searches files", "unrelated cooking recipe"])
    query = index.vectorize("retrieval manager")
    close = index.vectorize("retrieval manager searches files")
    far = index.vectorize("unrelated cooking recipe")
    assert cosine_similarity(query, close) > cosine_similarity(query, far)


# --- router -----------------------------------------------------------

def test_router_prefers_exact_for_symbol_lookup() -> None:
    assert route("Where is _compose_prompt?") == "exact"


def test_router_prefers_semantic_for_conceptual_question() -> None:
    assert route("How does meeting orchestration work?") == "semantic"


def test_router_uses_hybrid_when_both_signals_present() -> None:
    assert route("Explain _compose_prompt's role in meetings") == "hybrid"


# --- indexer -----------------------------------------------------------

def test_index_build_and_incremental_update(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    _write(project, "a.py", "def alpha():\n    pass\n")
    db_path = tmp_path / "index.sqlite"
    connection = open_index(db_path)
    try:
        build_index(connection, project)
        assert connection.execute("SELECT COUNT(*) FROM chunks").fetchone()[0] == 1

        path_b = _write(project, "b.py", "def beta():\n    pass\n")
        update_file(connection, project, path_b)
        connection.commit()
        assert connection.execute("SELECT COUNT(*) FROM files").fetchone()[0] == 2
    finally:
        connection.close()


def test_index_deletes_stale_files_on_rebuild(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    path = _write(project, "a.py", "def alpha():\n    pass\n")
    db_path = tmp_path / "index.sqlite"
    connection = open_index(db_path)
    try:
        build_index(connection, project)
        path.unlink()
        build_index(connection, project)
        assert connection.execute("SELECT COUNT(*) FROM files").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM chunks").fetchone()[0] == 0
    finally:
        connection.close()


def test_semantic_search_returns_source_line_metadata(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    _write(project, "notes.md", "# Meeting routing\n\nMeetings are routed by the orchestrator.\n")
    db_path = tmp_path / "index.sqlite"
    connection = open_index(db_path)
    try:
        build_index(connection, project)
        results = semantic_search(connection, "meeting routing")
        assert results
        assert results[0].path == "notes.md"
        assert results[0].start_line >= 1
    finally:
        connection.close()


# --- reranker / manager -------------------------------------------------

def test_merge_and_rerank_dedupes_overlapping_hits() -> None:
    class FakeSemantic:
        path, start_line, end_line, content, score = "a.py", 1, 5, "def alpha(): pass", 0.4

    class FakeGrep:
        path, line, text, score = "a.py", 2, "def alpha(): pass", 1.0

    ranked = merge_and_rerank([FakeGrep()], [FakeSemantic()], {"alpha"}, top_k=5)
    assert len(ranked) == 1
    assert set(ranked[0].sources) == {"semantic", "exact"}


def test_retrieval_manager_read_file_enforces_project_root(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    _write(project, "a.py", "line1\nline2\nline3\n")
    manager = RetrievalManager(project, tmp_path / "index.sqlite")
    assert manager.read_file("a.py")["content"] == "line1\nline2\nline3"
    assert manager.read_file("../outside.py") is None


def test_retrieval_manager_has_no_write_capability() -> None:
    """Critical security test: Chat mode's only capability surface must stay read-only."""
    manager_methods = {name for name in dir(RetrievalManager) if not name.startswith("_")}
    forbidden = {"write_file", "edit_file", "delete_file", "execute", "shell", "run_command", "git"}
    assert not (manager_methods & forbidden)
