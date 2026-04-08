"""
mempalace_client.py — Optional persistent memory layer for githubFixer.

Wraps the mempalace Python API. All public methods are safe no-ops when
mempalace is not installed or MEMPALACE_ENABLED=false in config.

Install:
    pip install git+https://github.com/milla-jovovich/mempalace

Initialise a palace (one-time):
    python3 -c "from mempalace.config import MempalaceConfig; MempalaceConfig().init()"

Then enable in .env:
    MEMPALACE_ENABLED=true
"""
from __future__ import annotations

import hashlib
import logging
import subprocess
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Import cache — avoids re-probing on every call
# ---------------------------------------------------------------------------

_IMPORT_CHECKED: bool = False
_MEMPALACE_AVAILABLE: bool = False


def _mempalace_importable() -> bool:
    global _IMPORT_CHECKED, _MEMPALACE_AVAILABLE
    if not _IMPORT_CHECKED:
        try:
            import mempalace  # noqa: F401
            _MEMPALACE_AVAILABLE = True
        except ImportError:
            _MEMPALACE_AVAILABLE = False
            logger.debug(
                "mempalace not installed — memory features disabled. "
                "Install: pip install git+https://github.com/milla-jovovich/mempalace"
            )
        _IMPORT_CHECKED = True
    return _MEMPALACE_AVAILABLE


def _reset_import_cache() -> None:
    """Reset the import check cache. Used in tests to isolate each test case."""
    global _IMPORT_CHECKED, _MEMPALACE_AVAILABLE
    _IMPORT_CHECKED = False
    _MEMPALACE_AVAILABLE = False


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

class MemPalaceClient:
    """Thin wrapper around the mempalace Python API for cross-run agent memory.

    Actual mempalace API surface used:
      - mempalace.searcher.search_memories(query, palace_path, wing, room, n_results)
          → dict: {"query": str, "filters": dict, "results": [{"text", "wing", "room",
                   "source_file", "similarity"}]}
      - mempalace.miner.get_collection(palace_path)
          → chromadb collection object
      - mempalace.miner.add_drawer(collection, wing, room, content, source_file,
                                   chunk_index, agent)
          → bool (True if added, False if duplicate)

    Usage in orchestrator:
        client = MemPalaceClient(settings.mempalace_palace_path)
        if client.is_available():
            cached = client.get_cached_analysis(repo_slug, commit_hash)
    """

    _AGENT_TAG = "githubFixer"
    _COLLECTION_NAME = "mempalace_drawers"

    def __init__(self, palace_path: str) -> None:
        self._palace_path = str(Path(palace_path).expanduser())
        self._available: bool | None = None

    # ── Availability ────────────────────────────────────────────────────────

    def is_available(self) -> bool:
        """Return True if mempalace is installed and the palace has been initialised."""
        if self._available is None:
            if not _mempalace_importable():
                self._available = False
            else:
                # Palace is ready when the chromadb directory exists
                palace = Path(self._palace_path)
                self._available = palace.exists() and palace.is_dir()
                if not self._available:
                    logger.debug(
                        "mempalace palace not found at %s. "
                        "Run: python3 -c \"from mempalace.config import MempalaceConfig; "
                        "MempalaceConfig().init()\"",
                        self._palace_path,
                    )
        return self._available

    # ── Internal: collection access ─────────────────────────────────────────

    def _get_collection(self):
        """Return the chromadb collection, creating it if needed."""
        from mempalace.miner import get_collection  # type: ignore[import]
        return get_collection(self._palace_path)

    # ── Analysis cache ──────────────────────────────────────────────────────

    def get_cached_analysis(self, repo_slug: str, commit_hash: str) -> str | None:
        """Return a cached codebase analysis for the exact commit hash, or None on miss."""
        if not self.is_available():
            return None
        try:
            from mempalace.searcher import search_memories  # type: ignore[import]
            result = search_memories(
                query=f"codebase analysis {commit_hash}",
                palace_path=self._palace_path,
                wing=repo_slug,
                room="analysis-cache",
                n_results=5,
            )
            if "error" in result:
                # Collection doesn't exist yet — that's fine on first run
                return None
            for hit in result.get("results", []):
                text = hit.get("text", "")
                if commit_hash in text:
                    # Strip the "COMMIT: <hash>" header line we prepend on write
                    lines = text.splitlines()
                    body = "\n".join(ln for ln in lines if not ln.startswith("COMMIT:")).strip()
                    return body or None
        except Exception as exc:
            logger.warning("mempalace: analysis cache lookup failed: %s", exc)
        return None

    def cache_analysis(self, repo_slug: str, commit_hash: str, analysis: str) -> None:
        """Persist a codebase analysis, keyed by commit hash."""
        if not self.is_available():
            return
        try:
            content = f"COMMIT: {commit_hash}\n\n{analysis}"
            self._write_drawer(
                content=content,
                wing=repo_slug,
                room="analysis-cache",
                source_label=f"codebase-analysis-{commit_hash}",
            )
            logger.debug("mempalace: cached analysis for %s @ %s", repo_slug, commit_hash)
        except Exception as exc:
            logger.warning("mempalace: cache_analysis write failed: %s", exc)

    # ── Context retrieval ───────────────────────────────────────────────────

    def get_prior_decisions(self, repo_slug: str, max_results: int = 3) -> str:
        """Return formatted prior decisions/implementations for this repo.

        Returns an empty string when nothing is found or on any error,
        so callers can safely append it to prompts without extra checks.
        """
        if not self.is_available():
            return ""
        try:
            from mempalace.searcher import search_memories  # type: ignore[import]
            result = search_memories(
                query="architectural decisions implementation approach",
                palace_path=self._palace_path,
                wing=repo_slug,
                room="implementations",
                n_results=max_results,
            )
            if "error" in result:
                return ""
            hits = result.get("results", [])
            if not hits:
                return ""
            parts: list[str] = []
            for hit in hits:
                text = hit.get("text", "").strip()
                similarity = hit.get("similarity", 0)
                if text and similarity >= 0.3:   # skip very low-relevance hits
                    parts.append(text)
            return "\n\n---\n\n".join(parts)
        except Exception as exc:
            logger.warning("mempalace: get_prior_decisions failed: %s", exc)
        return ""

    # ── Memory writes ───────────────────────────────────────────────────────

    def record_pr(
        self,
        repo_slug: str,
        issue_number: int,
        issue_title: str,
        spec: str,
        modified_files: list[str],
        pr_url: str,
    ) -> None:
        """Record a completed PR resolution into long-term memory."""
        if not self.is_available():
            return
        try:
            files_summary = "\n".join(f"- {f}" for f in sorted(set(modified_files))[:20])
            spec_excerpt = spec[:1500] if spec else "(no spec)"
            content = (
                f"Issue #{issue_number}: {issue_title}\n"
                f"PR: {pr_url}\n\n"
                f"## Spec\n{spec_excerpt}\n\n"
                f"## Modified Files\n{files_summary}"
            )
            self._write_drawer(
                content=content,
                wing=repo_slug,
                room="implementations",
                source_label=f"issue-{issue_number}",
            )
            logger.info("mempalace: recorded PR memory for %s issue #%d", repo_slug, issue_number)
        except Exception as exc:
            logger.warning("mempalace: record_pr failed: %s", exc)

    def record_failure(
        self,
        repo_slug: str,
        issue_number: int,
        reason: str,
        failures: list[dict],
    ) -> None:
        """Record a blocked/failed issue so future runs can learn from it."""
        if not self.is_available():
            return
        try:
            failure_lines = "\n".join(
                f"- {f.get('test', 'unknown')}: {f.get('error', '')}"
                for f in failures[:5]
            )
            content = (
                f"Issue #{issue_number} — BLOCKED\n"
                f"Reason: {reason}\n\n"
                f"## Test Failures\n"
                f"{failure_lines or '(no structured failures recorded)'}"
            )
            self._write_drawer(
                content=content,
                wing=repo_slug,
                room="failure-patterns",
                source_label=f"failure-issue-{issue_number}",
            )
            logger.debug("mempalace: recorded failure for %s issue #%d", repo_slug, issue_number)
        except Exception as exc:
            logger.warning("mempalace: record_failure failed: %s", exc)

    # ── Internal write helper ───────────────────────────────────────────────

    def _write_drawer(self, content: str, wing: str, room: str, source_label: str) -> None:
        """Write a single drawer to the palace via miner.add_drawer()."""
        from mempalace.miner import get_collection, add_drawer  # type: ignore[import]

        collection = get_collection(self._palace_path)
        chunk_index = 0
        add_drawer(
            collection=collection,
            wing=wing,
            room=room,
            content=content,
            source_file=source_label,
            chunk_index=chunk_index,
            agent=self._AGENT_TAG,
        )


# ---------------------------------------------------------------------------
# Standalone helper (used by orchestrator, no class needed)
# ---------------------------------------------------------------------------

def get_head_commit_hash(repo_path: str | Path) -> str:
    """Return the short (12-char) HEAD commit hash for the given repo path.

    Returns an empty string on failure so callers can skip caching gracefully.
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(repo_path),
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            return result.stdout.strip()[:12]
    except Exception as exc:
        logger.debug("mempalace: could not read HEAD commit hash: %s", exc)
    return ""
