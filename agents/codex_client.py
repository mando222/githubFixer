"""
CodexClient — subprocess adapter for the OpenAI Codex CLI.

Implements the same async context-manager + streaming interface as AnthropicAPIClient
so that _run_agent() in orchestrator.py works without modification.

Usage:
    async with CodexClient(options) as client:
        await client.query(task_prompt)
        async for message in client.receive_response():
            ...
"""
from __future__ import annotations

import asyncio
import shutil
from dataclasses import dataclass

from agents.types import AssistantMessage, ResultMessage, TextBlock


@dataclass
class CodexClientOptions:
    system_prompt: str
    model: str          # OpenAI model name, e.g. "o4-mini", "o3", "gpt-4.1"
    cwd: str            # Absolute path to the repository working directory
    timeout: float = 600.0  # Per-call subprocess timeout in seconds


class CodexClient:
    """Async subprocess wrapper around the Codex CLI.

    Implements the agent client interface used in orchestrator._run_agent():
      - async context manager (__aenter__ / __aexit__)
      - query(prompt)        — stores the task prompt
      - receive_response()   — async generator yielding AssistantMessage / ResultMessage
    """

    def __init__(self, options: CodexClientOptions) -> None:
        self._options = options
        self._task_prompt: str | None = None
        self._proc: asyncio.subprocess.Process | None = None

    # ── Async context manager ──────────────────────────────────────────────

    async def __aenter__(self) -> "CodexClient":
        _check_codex_installed()
        return self

    async def __aexit__(self, *_: object) -> None:
        if self._proc is not None and self._proc.returncode is None:
            try:
                self._proc.kill()
                await self._proc.wait()
            except ProcessLookupError:
                pass
        self._proc = None

    # ── Query / receive_response ───────────────────────────────────────────

    async def query(self, prompt: str) -> None:
        """Store the task prompt for the upcoming receive_response() call."""
        self._task_prompt = prompt

    async def receive_response(self):  # type: ignore[return]
        """Async generator — spawn the Codex subprocess and yield messages.

        Yields:
            AssistantMessage — one message containing the full stdout as a TextBlock
            ResultMessage    — signals completion (always a success if we get here)
        """
        if self._task_prompt is None:
            return

        opts = self._options
        cmd: list[str] = [
            "codex",
            "--approval-mode", "full-auto",
            "--model", opts.model,
            "--system-prompt", opts.system_prompt,
            self._task_prompt,
        ]

        self._proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=opts.cwd,
        )

        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                self._proc.communicate(),
                timeout=opts.timeout,
            )
        except asyncio.TimeoutError:
            self._proc.kill()
            await self._proc.wait()
            raise RuntimeError(
                f"Codex CLI timed out after {opts.timeout:.0f}s "
                f"(model={opts.model})"
            )

        if self._proc.returncode != 0:
            stderr_text = stderr_bytes.decode(errors="replace").strip()
            raise RuntimeError(
                f"Codex CLI exited with code {self._proc.returncode}: {stderr_text}"
            )

        text = stdout_bytes.decode(errors="replace")

        if text.strip():
            block = TextBlock(type="text", text=text)
            yield AssistantMessage(role="assistant", content=[block])

        yield ResultMessage(type="result", subtype="success")


# ── Helpers ────────────────────────────────────────────────────────────────

def _check_codex_installed() -> None:
    """Raise a descriptive error if the Codex CLI binary is not on PATH."""
    if shutil.which("codex") is None:
        raise RuntimeError(
            "Codex CLI not found on PATH.\n"
            "Install it with:  npm install -g @openai/codex\n"
            "Then set OPENAI_API_KEY in your environment and retry."
        )
