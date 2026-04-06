"""
AnthropicAPIClient — direct Anthropic API backend with a full agentic loop.

Implements the async context-manager + streaming interface expected by
_run_agent() in orchestrator.py.

Usage:
    async with AnthropicAPIClient(options) as client:
        await client.query(task_prompt)
        async for message in client.receive_response():
            ...
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import AsyncIterator

import anthropic

from agents.tools import TOOL_DEFINITIONS, execute_tool
from agents.types import (
    AssistantMessage,
    ResultMessage,
    TextBlock,
    ToolUseBlock,
)

# Max turns in a single agentic session before we bail out.
# Each turn = one assistant response (possibly with tool calls).
_MAX_TURNS = 100

# If the messages list grows beyond this many entries, prune middle turns
# to avoid hitting the context window limit.
_MAX_MESSAGES_BEFORE_PRUNE = 80


@dataclass
class AnthropicAPIClientOptions:
    system_prompt: str
    model: str              # Full Anthropic model ID, e.g. "claude-sonnet-4-6"
    tools: list[str]        # Subset of TOOL_DEFINITIONS keys; empty = text-only agent
    cwd: str                # Absolute path to repo working directory
    max_tokens: int = 16384
    api_key: str | None = None  # Falls back to ANTHROPIC_API_KEY env var if None


class AnthropicAPIClient:
    """Direct Anthropic API client with a self-contained agentic tool loop.

    Implements the agent client interface used in orchestrator._run_agent():
      - async context manager (__aenter__ / __aexit__)
      - query(prompt)        — stores the task prompt
      - receive_response()   — async generator running the agentic loop,
                               yielding AssistantMessage / ResultMessage
    After receive_response() completes, last_usage holds the cumulative token
    counts from the final API response (for token_tracker).
    """

    def __init__(self, options: AnthropicAPIClientOptions) -> None:
        self._options = options
        self._task_prompt: str | None = None
        self.last_usage: anthropic.types.Usage | None = None

    # ── Async context manager ──────────────────────────────────────────────

    async def __aenter__(self) -> "AnthropicAPIClient":
        return self

    async def __aexit__(self, *_: object) -> None:
        pass

    # ── Query / receive_response ───────────────────────────────────────────

    async def query(self, prompt: str) -> None:
        """Store the task prompt for the upcoming receive_response() call."""
        self._task_prompt = prompt

    async def receive_response(self):  # type: ignore[return]
        """Async generator — runs the full agentic tool loop.

        Yields:
            AssistantMessage — one per assistant turn (text + tool call blocks)
            ResultMessage    — one per tool execution result (or final completion)
        """
        if self._task_prompt is None:
            return

        opts = self._options
        api_key = opts.api_key or os.environ.get("ANTHROPIC_API_KEY")
        client = anthropic.AsyncAnthropic(api_key=api_key)

        tool_defs = [
            TOOL_DEFINITIONS[t]
            for t in opts.tools
            if t in TOOL_DEFINITIONS
        ]

        messages: list[dict] = [{"role": "user", "content": self._task_prompt}]
        turns = 0

        while turns < _MAX_TURNS:
            turns += 1

            # Prune conversation history if it grows too large (keeps first user
            # message + last N messages to stay within context limits).
            if len(messages) > _MAX_MESSAGES_BEFORE_PRUNE:
                messages = [messages[0]] + messages[-((_MAX_MESSAGES_BEFORE_PRUNE // 2)):]

            kwargs: dict = dict(
                model=opts.model,
                max_tokens=opts.max_tokens,
                system=opts.system_prompt,
                messages=messages,
            )
            if tool_defs:
                kwargs["tools"] = tool_defs

            response = await client.messages.create(**kwargs)
            self.last_usage = response.usage

            # Build typed content blocks for the yielded AssistantMessage
            content_blocks: list[TextBlock | ToolUseBlock] = []
            for block in response.content:
                if block.type == "text":
                    content_blocks.append(TextBlock(type="text", text=block.text))
                elif block.type == "tool_use":
                    content_blocks.append(
                        ToolUseBlock(
                            type="tool_use",
                            name=block.name,
                            id=block.id,
                            input=dict(block.input),
                        )
                    )

            yield AssistantMessage(role="assistant", content=content_blocks)

            # No tool calls or no tools available → we're done
            if response.stop_reason == "end_turn" or not tool_defs:
                yield ResultMessage(type="result", subtype="success")
                return

            if response.stop_reason != "tool_use":
                # Unexpected stop reason — treat as completion
                yield ResultMessage(type="result", subtype="success")
                return

            # Execute all tool_use blocks from this turn
            tool_results: list[dict] = []
            for block in response.content:
                if block.type != "tool_use":
                    continue

                output, is_error = await execute_tool(
                    block.name,
                    dict(block.input),
                    opts.cwd,
                )

                yield ResultMessage(
                    type="result",
                    subtype="error" if is_error else "success",
                    is_error=is_error,
                )

                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": output,
                    "is_error": is_error,
                })

            # Append both sides of the exchange and loop
            messages.append({"role": "assistant", "content": response.content})
            messages.append({"role": "user", "content": tool_results})

        # Exceeded max turns — return whatever we have
        yield ResultMessage(
            type="result",
            subtype="error",
            is_error=True,
        )
