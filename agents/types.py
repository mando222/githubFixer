"""
Local message type dataclasses shared by AnthropicAPIClient and CodexClient.

Using a common type hierarchy ensures isinstance() checks in _run_agent()
work identically regardless of which backend is active.
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
