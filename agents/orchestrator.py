from __future__ import annotations

import asyncio
import json
import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING

from arcade_config import get_arcade_mcp_config
from agents.definitions import (
    make_codebase_analyzer,
    make_coder,
    make_github_submitter,
    make_linear_tracker,
    make_planner,
)
from config import settings
from prompts import load_prompt
from state import IssueState, TaskState, clear_state, load_state, save_state
from workspace import issue_workspace

if TYPE_CHECKING:
    from models import IssueEvent

from typing import cast

try:
    from claude_agent_sdk import (  # type: ignore[import]
        AssistantMessage, ClaudeAgentOptions, ClaudeSDKClient, UserMessage,
    )
    from claude_agent_sdk.types import (  # type: ignore[import]
        HookCallback, HookMatcher, TextBlock, ToolResultBlock, ToolUseBlock,
    )
except ImportError:
    from anthropic.claude_agent_sdk import (  # type: ignore[import]
        AssistantMessage, ClaudeAgentOptions, ClaudeSDKClient, UserMessage,
    )
    from anthropic.claude_agent_sdk.types import (  # type: ignore[import]
        HookCallback, HookMatcher, TextBlock, ToolResultBlock, ToolUseBlock,
    )

from security import bash_security_hook

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Security settings                                                             #
# --------------------------------------------------------------------------- #

def _write_security_settings(workspace_dir: Path) -> Path:
    security_settings = {
        "permissions": {
            "defaultMode": "acceptEdits",
            "allow": [
                "Read(./**)",
                "Write(./**)",
                "Edit(./**)",
                "Glob(./**)",
                "Grep(./**)",
                "Bash(*)",
                "mcp__arcade__*",
            ],
        },
    }
    settings_file = workspace_dir / ".claude_settings.json"
    settings_file.write_text(json.dumps(security_settings, indent=2))
    return settings_file


# --------------------------------------------------------------------------- #
# Prompt builder                                                                #
# --------------------------------------------------------------------------- #

def _build_orchestrator_prompt(event: "IssueEvent", workspace_dir: Path, state: IssueState) -> str:
    repo_path = workspace_dir / "repo"

    # Build the "Already Completed" section from state
    completed_flags: list[str] = []
    if state.linear_issue_id:
        completed_flags.append(
            f"linear_issue_id={state.linear_issue_id!r} (Phase 1 DONE)"
        )
    if state.linear_project_id:
        completed_flags.append(f"linear_project_id={state.linear_project_id!r}")
    if state.analysis:
        completed_flags.append("analysis is SET (Phase 2 DONE — do not re-analyze)")
    if state.tasks:
        all_have_linear_id = all(t.linear_id for t in state.tasks)
        completed_flags.append(f"tasks are SET ({len(state.tasks)} tasks, Phase 3 DONE)")
        if all_have_linear_id:
            completed_flags.append("all Linear sub-issues created (Phase 4 DONE)")
    if state.tests_passed:
        completed_flags.append("tests_passed=true (Phase 5.5 DONE — do not re-run tests)")
    if state.pr_url:
        completed_flags.append(f"pr_url={state.pr_url!r} (Phase 6 DONE)")

    resume_section = ""
    if completed_flags:
        resume_section = (
            "\n## Already Completed (Resume)\n"
            + "\n".join(f"- {f}" for f in completed_flags)
            + "\n"
        )

    analysis_section = ""
    if state.analysis:
        analysis_section = f"\n## Codebase Analysis (from previous run)\n{state.analysis}\n"

    tasks_section = ""
    if state.tasks:
        tasks_json = json.dumps(
            [
                {
                    "title": t.title,
                    "description": t.description,
                    "linear_id": t.linear_id,
                    "status": t.status,
                }
                for t in state.tasks
            ],
            indent=2,
        )
        tasks_section = f"\n## Task Plan (from previous run)\n{tasks_json}\n"

    test_cycles_line = f"\n**Test cycles completed:** {state.test_cycles} / 2\n" if state.test_cycles > 0 else ""

    return f"""You are resolving GitHub issue #{event.number} from the repository {event.repo_full_name}.

**Issue Title:** {event.title}

**Issue Body:**
{event.body or "(no body provided)"}

**Repository:** {event.repo_full_name}
**Repo Owner:** {event.repo_owner}
**Repo Name:** {event.repo_name}
**Local Clone Path:** {repo_path}
**Issue URL:** {event.html_url}
**Branch to create:** {event.branch_name}
**Linear Team ID:** {settings.linear_team_id}
**Linear Project Name:** {event.repo_full_name}
{test_cycles_line}{resume_section}{analysis_section}{tasks_section}
Follow the phases in your system prompt in order. Skip any phase listed as already completed above.
Pass data explicitly between agents.
"""


# --------------------------------------------------------------------------- #
# GitHub issue comments                                                         #
# --------------------------------------------------------------------------- #

async def _post_issue_comment(event: "IssueEvent", body: str) -> None:
    proc = await asyncio.create_subprocess_exec(
        "gh", "issue", "comment", str(event.number),
        "--repo", event.repo_full_name,
        "--body", body,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        logger.warning(
            "Failed to comment on %s#%d: %s",
            event.repo_full_name, event.number, stderr.decode().strip(),
        )


# --------------------------------------------------------------------------- #
# State extraction                                                              #
# --------------------------------------------------------------------------- #

def _extract_state_updates(response_text: str) -> dict:
    """Find all STATE_UPDATE: {...} lines and merge them into one dict."""
    merged: dict = {}
    for match in re.finditer(r"STATE_UPDATE:\s*(\{[^\n]+\})", response_text):
        try:
            update = json.loads(match.group(1))
            merged.update(update)
        except json.JSONDecodeError:
            logger.warning(
                "Could not parse STATE_UPDATE JSON: %s", match.group(1)[:200]
            )
    return merged


def _apply_state_updates(state: IssueState, updates: dict) -> None:
    """Apply a parsed STATE_UPDATE dict onto the IssueState in-place."""
    if "step" in updates:
        state.step = updates["step"]
    if updates.get("linear_issue_id"):
        state.linear_issue_id = updates["linear_issue_id"]
    if updates.get("linear_project_id"):
        state.linear_project_id = updates["linear_project_id"]
    if updates.get("analysis"):
        state.analysis = updates["analysis"]
    if updates.get("pr_url"):
        state.pr_url = updates["pr_url"]
    if updates.get("modified_files"):
        state.modified_files = updates["modified_files"]
    if "test_cycles" in updates:
        state.test_cycles = int(updates["test_cycles"])
    if "tests_passed" in updates:
        state.tests_passed = bool(updates["tests_passed"])
    if "tasks" in updates and updates["tasks"]:
        state.tasks = [
            TaskState(
                title=t["title"],
                description=t["description"],
                linear_id=t.get("linear_id"),
                status=t.get("status", "todo"),
            )
            for t in updates["tasks"]
        ]


# --------------------------------------------------------------------------- #
# Client factory                                                                #
# --------------------------------------------------------------------------- #

def _make_client(repo_path: Path, settings_file: Path) -> ClaudeSDKClient:
    return ClaudeSDKClient(
        options=ClaudeAgentOptions(
            system_prompt=load_prompt("orchestrator"),
            model=settings.orchestrator_model,
            cwd=str(repo_path),
            settings=str(settings_file.resolve()),
            mcp_servers=get_arcade_mcp_config(),
            agents={
                "codebase-analyzer": make_codebase_analyzer(),
                "coder": make_coder(),
                "github-submitter": make_github_submitter(),
                "linear-tracker": make_linear_tracker(),
                "planner": make_planner(),
            },
            hooks={
                "PreToolUse": [
                    HookMatcher(
                        matcher="Bash",
                        hooks=[cast(HookCallback, bash_security_hook)],
                    ),
                ],
            },
        )
    )


# --------------------------------------------------------------------------- #
# Agent session runner                                                          #
# --------------------------------------------------------------------------- #

async def _run_agent_session(
    client: ClaudeSDKClient,
    prompt: str,
    event: "IssueEvent",
) -> str:
    """Run one agent session and return the full collected response text."""
    collected: list[str] = []
    async with client:
        await client.query(prompt)
        async for message in client.receive_response():
            _log_message(message, event)
            if isinstance(message, AssistantMessage):
                for block in getattr(message, "content", []):
                    if isinstance(block, TextBlock) and block.text:
                        collected.append(block.text)
    return "\n".join(collected)


# --------------------------------------------------------------------------- #
# Main workflow                                                                 #
# --------------------------------------------------------------------------- #

async def run_issue_workflow(event: "IssueEvent") -> None:
    logger.info(
        "Starting workflow for %s#%d: %s",
        event.repo_full_name, event.number, event.title,
    )

    # Phase 0: Load persisted state (resumes from last saved step)
    state = load_state(event)

    await _post_issue_comment(
        event,
        "🤖 **Auto-solver picked this up.** Analyzing the codebase and working on a fix...",
    )

    async with issue_workspace(event.repo_name, event.number, event.clone_url) as workspace_dir:
        repo_path = workspace_dir / "repo"
        settings_file = _write_security_settings(workspace_dir)

        client = _make_client(repo_path, settings_file)
        prompt = _build_orchestrator_prompt(event, workspace_dir, state)

        logger.info(
            "Running orchestrator for %s#%d (step=%s)",
            event.repo_full_name, event.number, state.step,
        )

        response_text = await _run_agent_session(client, prompt, event)

        # Extract and persist state from agent output
        updates = _extract_state_updates(response_text)
        if updates:
            _apply_state_updates(state, updates)
            save_state(event, state)
            logger.info(
                "State saved for %s#%d at step=%s",
                event.repo_full_name, event.number, state.step,
            )
        else:
            logger.warning(
                "No STATE_UPDATE lines found in response for %s#%d — state not updated",
                event.repo_full_name, event.number,
            )

    # Clear state only on full completion
    if state.step == "complete":
        clear_state(event)
        logger.info("Workflow complete for %s#%d", event.repo_full_name, event.number)
    else:
        logger.info(
            "Workflow ended at step=%s for %s#%d — state preserved for resume",
            state.step, event.repo_full_name, event.number,
        )


# --------------------------------------------------------------------------- #
# Message logging                                                               #
# --------------------------------------------------------------------------- #

def _log_message(message: object, event: "IssueEvent") -> None:
    tag = f"[{event.repo_full_name}#{event.number}]"

    if isinstance(message, AssistantMessage):
        for block in getattr(message, "content", []):
            if isinstance(block, TextBlock) and block.text.strip():
                logger.info("%s %s", tag, block.text.strip()[:300])
            elif isinstance(block, ToolUseBlock):
                inp = getattr(block, "input", {})
                brief = str(inp)[:200] if inp else ""
                logger.info("%s → tool_use: %s  input: %s", tag, block.name, brief)

    elif isinstance(message, UserMessage):
        for block in getattr(message, "content", []):
            if isinstance(block, ToolResultBlock):
                if getattr(block, "is_error", False):
                    logger.warning(
                        "%s ← TOOL ERROR (tool_use_id=%s): %s",
                        tag,
                        getattr(block, "tool_use_id", "?"),
                        str(block.content)[:500],
                    )
                else:
                    content_preview = str(getattr(block, "content", ""))[:150]
                    logger.info("%s ← tool_result OK: %s", tag, content_preview)

    else:
        msg_type = getattr(message, "type", type(message).__name__)
        if msg_type not in ("system", "rate_limit"):
            logger.debug("%s [%s]", tag, msg_type)
