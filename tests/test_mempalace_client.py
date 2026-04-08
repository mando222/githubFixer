"""Unit tests for mempalace_client.py.

All mempalace imports are mocked so these tests run without installing mempalace.
The actual mempalace API shapes are:
  - search_memories() → {"query": str, "filters": dict,
                         "results": [{"text", "wing", "room", "source_file", "similarity"}]}
  - get_collection(palace_path) → chromadb collection
  - add_drawer(collection, wing, room, content, source_file, chunk_index, agent) → bool
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_search_result(hits: list[dict]) -> dict:
    """Build a fake search_memories() return value."""
    return {"query": "test", "filters": {}, "results": hits}


def _make_hit(text: str, similarity: float = 0.9) -> dict:
    return {"text": text, "wing": "repo", "room": "room", "source_file": "x", "similarity": similarity}


# ---------------------------------------------------------------------------
# is_available
# ---------------------------------------------------------------------------

class TestIsAvailable:
    def test_returns_false_when_import_fails(self, tmp_path):
        from mempalace_client import MemPalaceClient, _reset_import_cache
        _reset_import_cache()
        with patch("mempalace_client._mempalace_importable", return_value=False):
            client = MemPalaceClient(str(tmp_path))
            assert client.is_available() is False

    def test_returns_false_when_palace_dir_missing(self, tmp_path):
        from mempalace_client import MemPalaceClient, _reset_import_cache
        _reset_import_cache()
        missing = tmp_path / "no_palace_here"
        with patch("mempalace_client._mempalace_importable", return_value=True):
            client = MemPalaceClient(str(missing))
            assert client.is_available() is False

    def test_returns_true_when_installed_and_palace_exists(self, tmp_path):
        from mempalace_client import MemPalaceClient, _reset_import_cache
        _reset_import_cache()
        palace = tmp_path / "palace"
        palace.mkdir()
        with patch("mempalace_client._mempalace_importable", return_value=True):
            client = MemPalaceClient(str(palace))
            assert client.is_available() is True

    def test_result_is_cached(self, tmp_path):
        """is_available() should only evaluate _mempalace_importable once."""
        from mempalace_client import MemPalaceClient, _reset_import_cache
        _reset_import_cache()
        palace = tmp_path / "palace"
        palace.mkdir()
        with patch("mempalace_client._mempalace_importable", return_value=True) as mock_imp:
            client = MemPalaceClient(str(palace))
            client.is_available()
            client.is_available()
            client.is_available()
        assert mock_imp.call_count <= 1


# ---------------------------------------------------------------------------
# get_cached_analysis
# ---------------------------------------------------------------------------

class TestGetCachedAnalysis:
    def _available_client(self, tmp_path):
        from mempalace_client import MemPalaceClient, _reset_import_cache
        _reset_import_cache()
        palace = tmp_path / "palace"
        palace.mkdir()
        with patch("mempalace_client._mempalace_importable", return_value=True):
            client = MemPalaceClient(str(palace))
        return client

    def test_returns_none_when_not_available(self, tmp_path):
        from mempalace_client import MemPalaceClient, _reset_import_cache
        _reset_import_cache()
        with patch("mempalace_client._mempalace_importable", return_value=False):
            client = MemPalaceClient(str(tmp_path))
            assert client.get_cached_analysis("repo", "abc123") is None

    def test_returns_none_on_cache_miss(self, tmp_path):
        client = self._available_client(tmp_path)
        mock_search = MagicMock(return_value=_make_search_result([]))
        with patch.dict(sys.modules, {
            "mempalace.searcher": MagicMock(search_memories=mock_search)
        }):
            result = client.get_cached_analysis("my-repo", "abc123def456")
        assert result is None

    def test_returns_none_on_search_error(self, tmp_path):
        client = self._available_client(tmp_path)
        mock_search = MagicMock(return_value={"error": "collection not found"})
        with patch.dict(sys.modules, {
            "mempalace.searcher": MagicMock(search_memories=mock_search)
        }):
            result = client.get_cached_analysis("my-repo", "abc123def456")
        assert result is None

    def test_returns_body_on_cache_hit(self, tmp_path):
        client = self._available_client(tmp_path)
        commit = "abc123def456"
        body = "Language: Python\nArchitecture: Django"
        hit = _make_hit(f"COMMIT: {commit}\n\n{body}")
        mock_search = MagicMock(return_value=_make_search_result([hit]))
        with patch.dict(sys.modules, {
            "mempalace.searcher": MagicMock(search_memories=mock_search)
        }):
            result = client.get_cached_analysis("my-repo", commit)
        assert result == body

    def test_strips_commit_header_line(self, tmp_path):
        client = self._available_client(tmp_path)
        commit = "aabbccddeeff"
        body = "Line1\nLine2"
        hit = _make_hit(f"COMMIT: {commit}\n\n{body}")
        mock_search = MagicMock(return_value=_make_search_result([hit]))
        with patch.dict(sys.modules, {
            "mempalace.searcher": MagicMock(search_memories=mock_search)
        }):
            result = client.get_cached_analysis("my-repo", commit)
        assert "COMMIT:" not in result
        assert body in result

    def test_returns_none_when_different_commit(self, tmp_path):
        client = self._available_client(tmp_path)
        hit = _make_hit("COMMIT: differenthash\n\nsome analysis")
        mock_search = MagicMock(return_value=_make_search_result([hit]))
        with patch.dict(sys.modules, {
            "mempalace.searcher": MagicMock(search_memories=mock_search)
        }):
            result = client.get_cached_analysis("my-repo", "abc123def456")
        assert result is None

    def test_returns_none_on_exception(self, tmp_path):
        client = self._available_client(tmp_path)
        mock_search = MagicMock(side_effect=RuntimeError("db error"))
        with patch.dict(sys.modules, {
            "mempalace.searcher": MagicMock(search_memories=mock_search)
        }):
            result = client.get_cached_analysis("my-repo", "abc123")
        assert result is None


# ---------------------------------------------------------------------------
# cache_analysis
# ---------------------------------------------------------------------------

class TestCacheAnalysis:
    def test_noop_when_not_available(self, tmp_path):
        from mempalace_client import MemPalaceClient, _reset_import_cache
        _reset_import_cache()
        with patch("mempalace_client._mempalace_importable", return_value=False):
            client = MemPalaceClient(str(tmp_path))
            # Should not raise
            client.cache_analysis("repo", "abc123", "analysis text")

    def test_calls_write_drawer_with_commit_hash(self, tmp_path):
        from mempalace_client import MemPalaceClient, _reset_import_cache
        _reset_import_cache()
        palace = tmp_path / "palace"
        palace.mkdir()
        with patch("mempalace_client._mempalace_importable", return_value=True):
            client = MemPalaceClient(str(palace))
        with patch.object(client, "_write_drawer") as mock_write:
            client.cache_analysis("my-repo", "abc123def456", "analysis text")
        mock_write.assert_called_once()
        kwargs = mock_write.call_args.kwargs
        assert "abc123def456" in kwargs["content"]
        assert kwargs["room"] == "analysis-cache"
        assert kwargs["wing"] == "my-repo"


# ---------------------------------------------------------------------------
# get_prior_decisions
# ---------------------------------------------------------------------------

class TestGetPriorDecisions:
    def _client(self, tmp_path):
        from mempalace_client import MemPalaceClient, _reset_import_cache
        _reset_import_cache()
        palace = tmp_path / "palace"
        palace.mkdir()
        with patch("mempalace_client._mempalace_importable", return_value=True):
            return MemPalaceClient(str(palace))

    def test_returns_empty_string_when_not_available(self, tmp_path):
        from mempalace_client import MemPalaceClient, _reset_import_cache
        _reset_import_cache()
        with patch("mempalace_client._mempalace_importable", return_value=False):
            client = MemPalaceClient(str(tmp_path))
            assert client.get_prior_decisions("my-repo") == ""

    def test_returns_empty_on_no_results(self, tmp_path):
        client = self._client(tmp_path)
        mock_search = MagicMock(return_value=_make_search_result([]))
        with patch.dict(sys.modules, {
            "mempalace.searcher": MagicMock(search_memories=mock_search)
        }):
            assert client.get_prior_decisions("my-repo") == ""

    def test_returns_empty_on_search_error(self, tmp_path):
        client = self._client(tmp_path)
        mock_search = MagicMock(return_value={"error": "no collection"})
        with patch.dict(sys.modules, {
            "mempalace.searcher": MagicMock(search_memories=mock_search)
        }):
            assert client.get_prior_decisions("my-repo") == ""

    def test_returns_text_from_hits(self, tmp_path):
        client = self._client(tmp_path)
        hits = [_make_hit("Used Django ORM for all DB access", similarity=0.85)]
        mock_search = MagicMock(return_value=_make_search_result(hits))
        with patch.dict(sys.modules, {
            "mempalace.searcher": MagicMock(search_memories=mock_search)
        }):
            result = client.get_prior_decisions("my-repo")
        assert "Django ORM" in result

    def test_skips_low_similarity_hits(self, tmp_path):
        client = self._client(tmp_path)
        hits = [_make_hit("Irrelevant content", similarity=0.1)]
        mock_search = MagicMock(return_value=_make_search_result(hits))
        with patch.dict(sys.modules, {
            "mempalace.searcher": MagicMock(search_memories=mock_search)
        }):
            result = client.get_prior_decisions("my-repo")
        assert result == ""

    def test_returns_empty_on_exception(self, tmp_path):
        client = self._client(tmp_path)
        mock_search = MagicMock(side_effect=RuntimeError("search failed"))
        with patch.dict(sys.modules, {
            "mempalace.searcher": MagicMock(search_memories=mock_search)
        }):
            assert client.get_prior_decisions("my-repo") == ""


# ---------------------------------------------------------------------------
# record_pr / record_failure
# ---------------------------------------------------------------------------

class TestRecordMethods:
    def _client(self, tmp_path):
        from mempalace_client import MemPalaceClient, _reset_import_cache
        _reset_import_cache()
        palace = tmp_path / "palace"
        palace.mkdir()
        with patch("mempalace_client._mempalace_importable", return_value=True):
            return MemPalaceClient(str(palace))

    def test_record_pr_noop_when_not_available(self, tmp_path):
        from mempalace_client import MemPalaceClient, _reset_import_cache
        _reset_import_cache()
        with patch("mempalace_client._mempalace_importable", return_value=False):
            client = MemPalaceClient(str(tmp_path))
            client.record_pr("repo", 1, "title", "spec", [], "https://github.com/pr/1")

    def test_record_pr_calls_write_drawer_with_correct_room(self, tmp_path):
        client = self._client(tmp_path)
        with patch.object(client, "_write_drawer") as mock_write:
            client.record_pr(
                "my-repo", 99, "Add feature X", "spec text",
                ["src/foo.py"], "https://github.com/pr/99",
            )
        mock_write.assert_called_once()
        kwargs = mock_write.call_args.kwargs
        assert kwargs["room"] == "implementations"
        assert kwargs["wing"] == "my-repo"
        assert "#99" in kwargs["content"]
        assert "https://github.com/pr/99" in kwargs["content"]

    def test_record_pr_includes_spec_excerpt(self, tmp_path):
        client = self._client(tmp_path)
        with patch.object(client, "_write_drawer") as mock_write:
            client.record_pr("r", 1, "t", "my spec content", [], "url")
        content = mock_write.call_args.kwargs["content"]
        assert "my spec content" in content

    def test_record_failure_calls_write_drawer_with_correct_room(self, tmp_path):
        client = self._client(tmp_path)
        with patch.object(client, "_write_drawer") as mock_write:
            client.record_failure(
                "my-repo", 42, "Tests failed after 3 cycles",
                [{"test": "test_foo", "error": "AssertionError: x != y"}],
            )
        mock_write.assert_called_once()
        kwargs = mock_write.call_args.kwargs
        assert kwargs["room"] == "failure-patterns"
        assert kwargs["wing"] == "my-repo"
        assert "#42" in kwargs["content"]
        assert "Tests failed" in kwargs["content"]

    def test_record_failure_includes_test_details(self, tmp_path):
        client = self._client(tmp_path)
        with patch.object(client, "_write_drawer") as mock_write:
            client.record_failure("r", 1, "reason", [
                {"test": "test_bar", "error": "KeyError: foo"}
            ])
        content = mock_write.call_args.kwargs["content"]
        assert "test_bar" in content
        assert "KeyError" in content

    def test_methods_silently_swallow_write_errors(self, tmp_path):
        client = self._client(tmp_path)
        with patch.object(client, "_write_drawer", side_effect=RuntimeError("disk full")):
            client.record_pr("r", 1, "t", "s", [], "url")
            client.record_failure("r", 1, "reason", [])


# ---------------------------------------------------------------------------
# _write_drawer — uses actual miner API shape
# ---------------------------------------------------------------------------

class TestWriteDrawer:
    def test_calls_add_drawer_with_correct_args(self, tmp_path):
        from mempalace_client import MemPalaceClient, _reset_import_cache
        _reset_import_cache()
        palace = tmp_path / "palace"
        palace.mkdir()
        with patch("mempalace_client._mempalace_importable", return_value=True):
            client = MemPalaceClient(str(palace))

        mock_collection = MagicMock()
        mock_get_collection = MagicMock(return_value=mock_collection)
        mock_add_drawer = MagicMock(return_value=True)
        mock_miner = MagicMock(
            get_collection=mock_get_collection,
            add_drawer=mock_add_drawer,
        )
        with patch.dict(sys.modules, {"mempalace.miner": mock_miner}):
            client._write_drawer(
                content="test content",
                wing="my-repo",
                room="implementations",
                source_label="issue-42",
            )

        mock_get_collection.assert_called_once_with(client._palace_path)
        mock_add_drawer.assert_called_once_with(
            collection=mock_collection,
            wing="my-repo",
            room="implementations",
            content="test content",
            source_file="issue-42",
            chunk_index=0,
            agent="githubFixer",
        )


# ---------------------------------------------------------------------------
# get_head_commit_hash
# ---------------------------------------------------------------------------

class TestGetHeadCommitHash:
    def test_returns_12_char_hash(self, tmp_path):
        from mempalace_client import get_head_commit_hash
        fake_hash = "abcdef1234567890full"
        with patch("mempalace_client.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=fake_hash + "\n")
            result = get_head_commit_hash(tmp_path)
        assert result == fake_hash[:12]
        assert len(result) == 12

    def test_returns_empty_on_nonzero_returncode(self, tmp_path):
        from mempalace_client import get_head_commit_hash
        with patch("mempalace_client.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=128, stdout="")
            result = get_head_commit_hash(tmp_path)
        assert result == ""

    def test_returns_empty_on_exception(self, tmp_path):
        from mempalace_client import get_head_commit_hash
        with patch("mempalace_client.subprocess.run", side_effect=OSError("git not found")):
            result = get_head_commit_hash(tmp_path)
        assert result == ""

    def test_passes_correct_cwd(self, tmp_path):
        from mempalace_client import get_head_commit_hash
        with patch("mempalace_client.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="abc123\n")
            get_head_commit_hash(tmp_path)
        assert mock_run.call_args.kwargs["cwd"] == str(tmp_path)
