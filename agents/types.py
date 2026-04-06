"""
Local message type dataclasses — replaces claude_agent_sdk message types.

These are used by both AnthropicAPIClient and CodexClient so that the
isinstance() checks in _run_agent() work without any changes to that function.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class TextBlock:
    type: str   # always "text"
    text: str


@dataclass
class ToolUseBlock:
    """Assistant tool-call block.  Has a .name attr so the existing
    `elif hasattr(block, "name")` check in _run_agent keeps working."""
    type: str   # always "tool_use"
    name: str
    id: str
    input: dict = field(default_factory=dict)


@dataclass
class AssistantMessage:
    role: str           # always "assistant"
    content: list       # list of TextBlock | ToolUseBlock


@dataclass
class ResultMessage:
    type: str           # "result"
    subtype: str        # "success" | "error"
    is_error: bool = False


@dataclass
class RateLimitEvent:
    """Placeholder — the Anthropic API does not emit rate-limit stream events.
    Kept so the isinstance branch in _run_agent is never triggered (we never yield it)."""
    pass
