from __future__ import annotations

import asyncio
import logging
import shutil
from contextlib import asynccontextmanager
from pathlib import Path

logger = logging.getLogger(__name__)

WORKSPACE_ROOT = Path("/tmp/issue-solver")


@asynccontextmanager
async def issue_workspace(repo_name: str, issue_number: int, clone_url: str):
    """
    Context manager that clones the target repo into a temp directory,
    yields the workspace path, and cleans up on exit.

    Usage:
        async with issue_workspace(repo_name, issue_number, clone_url) as workspace:
            repo_path = workspace / "repo"
    """
    workspace = WORKSPACE_ROOT / f"{repo_name}-{issue_number}"
    repo_path = workspace / "repo"

    # Remove any leftover workspace from a previous failed run before starting.
    shutil.rmtree(workspace, ignore_errors=True)
    workspace.mkdir(parents=True, exist_ok=True)

    success = False
    try:
        # Use `gh repo clone` so private repos work via the user's existing
        # gh auth — no separate token needed.
        full_name = clone_url.replace("https://github.com/", "").removesuffix(".git")
        logger.info("Cloning %s into %s", full_name, repo_path)
        proc = await asyncio.create_subprocess_exec(
            "gh", "repo", "clone", full_name, str(repo_path), "--", "--depth", "1",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(
                f"git clone failed (exit {proc.returncode}): {stderr.decode()}"
            )
        logger.info("Clone complete: %s", repo_path)
        yield workspace
        success = True
    finally:
        if success:
            logger.info("Cleaning up workspace: %s", workspace)
            shutil.rmtree(workspace, ignore_errors=True)
        else:
            logger.info(
                "Workflow did not complete — preserving workspace for inspection: %s",
                workspace,
            )
