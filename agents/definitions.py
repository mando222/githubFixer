from __future__ import annotations

from typing import Literal

from linear_config import LINEAR_TOOLS
from config import settings
from prompts import load_prompt

# claude_agent_sdk import — adjust if the package name differs on install
try:
    from claude_agent_sdk import AgentDefinition  # type: ignore[import]
except ImportError:
    from anthropic.types.beta import AgentDefinition  # type: ignore[import]

ModelShortname = Literal["sonnet", "opus", "haiku", "inherit"]

# Full model IDs for direct (non-subagent) invocation
AGENT_MODELS: dict[str, str] = {
    "codebase-analyzer": settings.analyzer_agent_model,
    "coder": settings.coding_agent_model,
    "tester": settings.tester_agent_model,
    "reviewer": settings.reviewer_agent_model,
    "github-submitter": settings.github_agent_model,
    "linear-tracker": settings.linear_agent_model,
    "planner": settings.planner_agent_model,
}


def _shortname(model: str) -> ModelShortname:
    """Map a full Claude model string to the AgentDefinition shortname literal."""
    m = model.lower()
    if "haiku" in m:
        return "haiku"
    if "sonnet" in m:
        return "sonnet"
    if "opus" in m:
        return "opus"
    return "inherit"


def make_codebase_analyzer() -> AgentDefinition:
    return AgentDefinition(
        description=(
            "Deep codebase analyst. Use this FIRST to understand the repository "
            "structure, identify relevant files, and propose an implementation approach "
            "for the issue. Returns a structured analysis report with exact file paths."
        ),
        prompt=load_prompt("codebase_analyzer"),
        tools=["Read", "Glob", "Grep"],
        model=_shortname(settings.analyzer_agent_model),
    )


def make_coder() -> AgentDefinition:
    return AgentDefinition(
        description=(
            "Senior software engineer. Use AFTER codebase analysis to implement the fix "
            "or feature described in the issue. Writes and edits code, runs tests to "
            "validate, and reports the list of modified files. Does NOT commit or push."
        ),
        prompt=load_prompt("coder"),
        tools=["Read", "Write", "Edit", "Glob", "Grep", "Bash"],
        model=_shortname(settings.coding_agent_model),
    )


def make_github_submitter() -> AgentDefinition:
    return AgentDefinition(
        description=(
            "GitHub PR specialist. Use AFTER coding is complete to create a branch, "
            "commit all changes, push, and open a pull request. Returns the PR URL."
        ),
        prompt=load_prompt("github_submitter"),
        tools=["Bash", "Read"],
        model=_shortname(settings.github_agent_model),
    )


def make_linear_tracker() -> AgentDefinition:
    return AgentDefinition(
        description=(
            "Linear project management agent. Operations: "
            "A=create parent issue, B=mark In Review with PR URL, C=mark Needs Clarification, "
            "D=create sub-issue under parent, G=query existing issue state for resume. "
            "Returns Linear issue IDs or confirmation."
        ),
        prompt=load_prompt("linear_tracker"),
        tools=LINEAR_TOOLS,
        model=_shortname(settings.linear_agent_model),
    )


def make_tester() -> AgentDefinition:
    return AgentDefinition(
        description=(
            "Test runner. Use AFTER coding to run the full test suite and return structured "
            "JSON results. Does NOT modify files. Returns pass/fail status and failure details."
        ),
        prompt=load_prompt("tester"),
        tools=["Bash", "Read"],
        model=_shortname(settings.tester_agent_model),
    )


def make_reviewer() -> AgentDefinition:
    return AgentDefinition(
        description=(
            "Code reviewer. Use AFTER tests pass to verify the implementation addresses the "
            "issue. Reads git diff, checks correctness and completeness. Returns structured "
            "JSON with verdict (APPROVED/NEEDS_CHANGES) and issues list."
        ),
        prompt=load_prompt("reviewer"),
        tools=["Bash", "Read", "Glob", "Grep"],
        model=_shortname(settings.reviewer_agent_model),
    )


def make_planner() -> AgentDefinition:
    return AgentDefinition(
        description=(
            "Planning specialist. Use AFTER codebase analysis to break the issue into "
            "a concrete, ordered list of implementation tasks. Returns a raw JSON array "
            "of tasks, each with title, description, files_hint, and acceptance criteria. "
            "No tools needed — pure reasoning from the analysis provided."
        ),
        prompt=load_prompt("planner"),
        tools=[],
        model=_shortname(settings.planner_agent_model),
    )
