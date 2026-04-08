from __future__ import annotations

import asyncio
import logging
import os
import shutil
from contextlib import asynccontextmanager
from pathlib import Path

logger = logging.getLogger(__name__)

_GH_BIN: str = (
    shutil.which("gh", path=os.environ.get("PATH", "") + ":/opt/homebrew/bin:/usr/local/bin")
    or "gh"
)

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


async def _is_valid_git_repo(path: Path) -> bool:
    """Return True if path is a valid git repository."""
    if not path.exists():
        return False
    proc = await asyncio.create_subprocess_exec(
        "git", "rev-parse", "--git-dir",
        cwd=str(path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    await proc.communicate()
    return proc.returncode == 0


async def _ensure_base_clone(repo_name: str, full_name: str, base_path: Path) -> None:
    """Clone the repo once, or fetch to refresh an existing base clone."""
    if await _is_valid_git_repo(base_path):
        logger.info("Refreshing base clone for %s", full_name)
        await _run(["git", "fetch", "origin", "--depth", "1"], cwd=base_path)
    else:
        if base_path.exists():
            logger.warning(
                "Base clone for %s exists but is not a valid git repo — removing and re-cloning",
                full_name,
            )
            shutil.rmtree(base_path)
        logger.info("Creating base clone for %s at %s", full_name, base_path)
        base_path.parent.mkdir(parents=True, exist_ok=True)
        await _run(
            [_GH_BIN, "repo", "clone", full_name, str(base_path), "--", "--depth", "1"]
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

    # Prune stale worktree registrations before adding (handles leftover state
    # from a previous crashed run where the directory was deleted but the git
    # metadata was not cleaned up).
    try:
        await _run(["git", "worktree", "prune"], cwd=base_path)
    except RuntimeError:
        pass

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
