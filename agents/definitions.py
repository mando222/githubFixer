from __future__ import annotations

from dataclasses import dataclass, field

from config import settings
from prompts import load_prompt


@dataclass
class AgentDefinition:
    """Declarative spec for a sub-agent invocation."""
    description: str
    prompt: str
    tools: list[str]
    model: str  # Full Anthropic model ID, e.g. "claude-sonnet-4-6"


# Full model IDs for direct (non-subagent) invocation
AGENT_MODELS: dict[str, str] = {
    "codebase-analyzer": settings.analyzer_agent_model,
    "coder": settings.coding_agent_model,
    "tester": settings.tester_agent_model,
    "reviewer": settings.reviewer_agent_model,
    "github-submitter": settings.github_agent_model,
    "planner": settings.planner_agent_model,
    "spec-writer": settings.spec_writer_agent_model,
    "spec-reviewer": settings.spec_reviewer_agent_model,
}

# Codex model IDs for each agent type (used when agent_backend = "codex").
CODEX_AGENT_MODELS: dict[str, str] = {
    "codebase-analyzer": settings.codex_analyzer_model,
    "coder": settings.codex_coder_model,
    "tester": settings.codex_tester_model,
    "reviewer": settings.codex_reviewer_model,
    "github-submitter": settings.codex_github_model,
    "planner": settings.codex_planner_model,
    "spec-writer": settings.codex_spec_writer_model,
    "spec-reviewer": settings.codex_spec_reviewer_model,
}


def make_codebase_analyzer() -> AgentDefinition:
    return AgentDefinition(
        description=(
            "Deep codebase analyst. Use this FIRST to understand the repository "
            "structure, identify relevant files, and propose an implementation approach "
            "for the issue. Returns a structured analysis report with exact file paths."
        ),
        prompt=load_prompt("codebase_analyzer"),
        tools=["Read", "Glob", "Grep"],
        model=settings.analyzer_agent_model,
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
        model=settings.coding_agent_model,
    )


def make_github_submitter() -> AgentDefinition:
    return AgentDefinition(
        description=(
            "GitHub PR specialist. Use AFTER coding is complete to create a branch, "
            "commit all changes, push, and open a pull request. Returns the PR URL."
        ),
        prompt=load_prompt("github_submitter"),
        tools=["Bash", "Read"],
        model=settings.github_agent_model,
    )


def make_tester() -> AgentDefinition:
    return AgentDefinition(
        description=(
            "Test runner. Use AFTER coding to run the full test suite and return structured "
            "JSON results. Does NOT modify files. Returns pass/fail status and failure details."
        ),
        prompt=load_prompt("tester"),
        tools=["Bash", "Read"],
        model=settings.tester_agent_model,
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
        model=settings.reviewer_agent_model,
    )


def make_planner() -> AgentDefinition:
    return AgentDefinition(
        description=(
            "Planning specialist. Use AFTER spec writing and spec review to break the project "
            "spec into a concrete, ordered list of implementation tasks. Returns a raw JSON array "
            "of tasks, each with title, description, files_hint, and acceptance criteria. "
            "No tools needed — pure reasoning from the spec and analysis provided."
        ),
        prompt=load_prompt("planner"),
        tools=[],
        model=settings.planner_agent_model,
    )


def make_spec_writer() -> AgentDefinition:
    return AgentDefinition(
        description=(
            "Technical spec writer. Use AFTER codebase analysis and BEFORE planning to produce "
            "a complete, structured Markdown project specification from the GitHub issue and "
            "codebase analysis. Returns a spec using a consistent template, or AMBIGUOUS: with "
            "numbered clarifying questions if the issue is fundamentally underspecified. "
            "No tools needed — pure reasoning."
        ),
        prompt=load_prompt("spec_writer"),
        tools=[],
        model=settings.spec_writer_agent_model,
    )


def make_spec_reviewer() -> AgentDefinition:
    return AgentDefinition(
        description=(
            "Spec reviewer. Use AFTER spec writing (Phase 2.5) and BEFORE planning (Phase 3) "
            "to verify the spec completely covers all requirements from the original GitHub issue. "
            "Returns APPROVED (with rationale) or NEEDS_REVISION: with a numbered list of gaps. "
            "No tools needed — pure reasoning."
        ),
        prompt=load_prompt("spec_reviewer"),
        tools=[],
        model=settings.spec_reviewer_agent_model,
    )
