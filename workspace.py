from __future__ import annotations

import asyncio
import logging
import shutil
from contextlib import asynccontextmanager
from pathlib import Path

logger = logging.getLogger(__name__)

WORKSPACE_ROOT = Path("/tmp/issue-solver")
BASE_CLONES_ROOT = WORKSPACE_ROOT / "base"

# One lock per repo name prevents concurrent issues from racing on the initial clone.
_base_clone_locks: dict[str, asyncio.Lock] = {}


def _base_clone_lock(repo_name: str) -> asyncio.Lock:
    if repo_name not in _base_clone_locks:
        _base_clone_locks[repo_name] = asyncio.Lock()
    return _base_clone_locks[repo_name]


async def _run(cmd: list[str], cwd: Path | None = None) -> None:
    """Run a subprocess, raising RuntimeError on non-zero exit."""
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=str(cwd) if cwd else None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(
            f"{' '.join(cmd)} failed (exit {proc.returncode}): {stderr.decode()}"
        )


async def _ensure_base_clone(repo_name: str, full_name: str, base_path: Path) -> None:
    """Clone the repo once, or fetch to refresh an existing base clone."""
    if base_path.exists():
        logger.info("Refreshing base clone for %s", full_name)
        await _run(["git", "fetch", "origin", "--depth", "1"], cwd=base_path)
    else:
        logger.info("Creating base clone for %s at %s", full_name, base_path)
        base_path.parent.mkdir(parents=True, exist_ok=True)
        await _run(
            ["gh", "repo", "clone", full_name, str(base_path), "--", "--depth", "1"]
        )


@asynccontextmanager
async def issue_workspace(repo_name: str, issue_number: int, clone_url: str):
    """
    Context manager that provides an isolated git worktree for a single issue.

    A base clone of the repo is created once under /tmp/issue-solver/base/{repo_name}
    and reused across issues.  Each issue gets a lightweight worktree at
    /tmp/issue-solver/{repo_name}-{issue_number}/repo backed by the shared clone.

    Usage:
        async with issue_workspace(repo_name, issue_number, clone_url) as workspace:
            repo_path = workspace / "repo"
    """
    full_name = clone_url.replace("https://github.com/", "").removesuffix(".git")
    base_path = BASE_CLONES_ROOT / repo_name
    workspace = WORKSPACE_ROOT / f"{repo_name}-{issue_number}"
    repo_path = workspace / "repo"

    # Remove any stale worktree directory from a previous failed run.
    shutil.rmtree(workspace, ignore_errors=True)
    workspace.mkdir(parents=True, exist_ok=True)

    async with _base_clone_lock(repo_name):
        await _ensure_base_clone(repo_name, full_name, base_path)

    logger.info("Adding worktree for issue #%s at %s", issue_number, repo_path)
    await _run(
        ["git", "worktree", "add", "--detach", str(repo_path), "origin/HEAD"],
        cwd=base_path,
    )

    success = False
    try:
        yield workspace
        success = True
    finally:
        if success:
            logger.info("Removing worktree: %s", repo_path)
            await _run(
                ["git", "worktree", "remove", "--force", str(repo_path)],
                cwd=base_path,
            )
            await _run(["git", "worktree", "prune"], cwd=base_path)
            shutil.rmtree(workspace, ignore_errors=True)
        else:
            logger.info(
                "Workflow did not complete — preserving worktree for inspection: %s",
                workspace,
            )
            # Prune any other stale worktree metadata without touching this one.
            try:
                await _run(["git", "worktree", "prune"], cwd=base_path)
            except RuntimeError:
                pass
