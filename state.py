from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from models import IssueEvent

logger = logging.getLogger(__name__)

WorkflowStep = Literal[
    "created",
    "linear_setup",
    "analyzing",
    "planning",
    "creating_subtasks",
    "executing_tasks",
    "testing",
    "submitting_pr",
    "updating_linear",
    "complete",
]

STATE_DIR = Path.home() / ".github-fixer"


@dataclass
class TaskState:
    title: str
    description: str
    linear_id: str | None = None          # e.g., "MAN-43" — None until sub-issue created
    status: Literal["todo", "in_progress", "done"] = "todo"


@dataclass
class IssueState:
    issue_number: int
    repo_full_name: str                    # "owner/repo"
    step: WorkflowStep = "created"
    linear_issue_id: str | None = None    # parent issue, e.g., "MAN-42"
    linear_project_id: str | None = None  # Linear project UUID
    analysis: str | None = None           # full codebase-analyzer output
    tasks: list[TaskState] = field(default_factory=list)
    pr_url: str | None = None
    modified_files: list[str] = field(default_factory=list)
    test_cycles: int = 0          # number of test-and-remediate rounds completed
    tests_passed: bool = False    # True once the test suite passes clean

    @property
    def repo_owner(self) -> str:
        return self.repo_full_name.split("/")[0]

    @property
    def repo_name(self) -> str:
        return self.repo_full_name.split("/")[1]


# --------------------------------------------------------------------------- #
# Path helper                                                                   #
# --------------------------------------------------------------------------- #

def _state_path(owner: str, repo: str, issue_number: int) -> Path:
    return STATE_DIR / owner / repo / f"issue-{issue_number}.json"


# --------------------------------------------------------------------------- #
# Public API                                                                    #
# --------------------------------------------------------------------------- #

def load_state(event: "IssueEvent") -> IssueState:
    """Load persisted state, or return a fresh IssueState if none exists."""
    path = _state_path(event.repo_owner, event.repo_name, event.number)
    if not path.exists():
        logger.info(
            "No existing state for %s#%d — starting fresh",
            event.repo_full_name, event.number,
        )
        return IssueState(issue_number=event.number, repo_full_name=event.repo_full_name)

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        raw_tasks = data.pop("tasks", [])
        tasks = [TaskState(**t) for t in raw_tasks]
        state = IssueState(**data, tasks=tasks)
        logger.info(
            "Resumed state for %s#%d at step=%s",
            event.repo_full_name, event.number, state.step,
        )
        return state
    except Exception as exc:
        logger.warning(
            "Could not parse state file %s (%s) — starting fresh", path, exc
        )
        return IssueState(issue_number=event.number, repo_full_name=event.repo_full_name)


def save_state(event: "IssueEvent", state: IssueState) -> None:
    """Atomically write state to ~/.github-fixer/{owner}/{repo}/issue-{n}.json."""
    path = _state_path(event.repo_owner, event.repo_name, event.number)
    path.parent.mkdir(parents=True, exist_ok=True)

    data = asdict(state)  # handles nested TaskState → dict
    payload = json.dumps(data, indent=2).encode("utf-8")

    fd, tmp_path = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        os.write(fd, payload)
        os.fsync(fd)
        os.close(fd)
        os.replace(tmp_path, path)  # atomic on POSIX
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise

    logger.debug("State saved: %s (step=%s)", path, state.step)


def clear_state(event: "IssueEvent") -> None:
    """Delete the state file after successful completion."""
    path = _state_path(event.repo_owner, event.repo_name, event.number)
    try:
        path.unlink(missing_ok=True)
        logger.info("Cleared state for %s#%d", event.repo_full_name, event.number)
    except Exception as exc:
        logger.warning("Could not delete state file %s: %s", path, exc)
