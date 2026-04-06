> **Archived** — This migration is complete. This document is kept for historical reference only.

# Anthropic API Migration Plan

## Context

The `claude_agent_sdk` / Claude Code CLI is no longer available. This plan replaces every
SDK dependency with direct Anthropic API calls using the `anthropic` Python package.

The goal is **full functional equivalence**: the 8-agent orchestration pipeline continues to
work exactly as before — analyze → spec → plan → code → test → review → submit — but the
underlying execution engine shifts from the CLI to the API with a self-contained tool loop.

---

## What Changes

| Component | Before | After |
|---|---|---|
| Agent execution | `ClaudeSDKClient` (CLI subprocess) | `AnthropicAPIClient` (HTTP + tool loop) |
| Tool execution | CLI handles natively | Python executors in `agents/tools.py` |
| Agentic loop | CLI manages internally | Implemented in `AnthropicAPIClient` |
| Pre-tool security | SDK `HookMatcher` / `HookCallback` | Direct call to `validate_bash_command()` |
| Message types | SDK `AssistantMessage`, `TextBlock`, etc. | Local dataclasses in `agents/types.py` |
| `AgentDefinition` | SDK class | Local dataclass |
| Token tracking | `~/.claude/stats-cache.json` + `RateLimitEvent` | API `response.usage` |
| Auth | Claude Code CLI session | `ANTHROPIC_API_KEY` env var |
| `requirements.txt` | `claude-agent-sdk>=0.1.0` | `anthropic>=0.40.0` |

---

## New / Modified Files

| File | Change |
|---|---|
| `agents/types.py` | **New** — local message type dataclasses |
| `agents/tools.py` | **New** — tool definitions + Python executors |
| `agents/anthropic_client.py` | **New** — `AnthropicAPIClient` with full agentic loop |
| `agents/orchestrator.py` | Modify — remove SDK imports, wire new client |
| `agents/definitions.py` | Modify — remove SDK `AgentDefinition`, use local dataclass |
| `agents/codex_client.py` | Modify — import types from `agents/types` not SDK |
| `security.py` | Modify — remove SDK imports, keep pure-Python logic |
| `token_tracker.py` | Modify — replace SDK rate-limit tracking with API usage |
| `config.py` | Modify — add `anthropic_api_key`, `max_tokens_per_agent` |
| `requirements.txt` | Modify — swap `claude-agent-sdk` for `anthropic` |

---

## Step 1 — `agents/types.py` (New)

Local replacements for all SDK message/block types.  These are used in both
`AnthropicAPIClient` and `CodexClient` so that `isinstance()` checks in
`_run_agent()` continue to work without touching that function.

```python
from dataclasses import dataclass, field

@dataclass
class TextBlock:
    type: str     # always "text"
    text: str

@dataclass
class ToolUseBlock:
    """Represents an assistant tool-call block. Has a .name attr so the
    existing `elif hasattr(block, "name")` check in _run_agent keeps working."""
    type: str     # always "tool_use"
    name: str
    id: str
    input: dict

@dataclass
class AssistantMessage:
    role: str     # always "assistant"
    content: list # list of TextBlock | ToolUseBlock

@dataclass
class ResultMessage:
    type: str           # "result"
    subtype: str        # "success" | "error"
    is_error: bool = False

@dataclass
class RateLimitEvent:
    """Placeholder — Anthropic API does not emit rate-limit stream events.
    Kept so _run_agent's isinstance branch is never triggered (we just never yield it)."""
    pass
```

---

## Step 2 — `agents/tools.py` (New)

Tool JSON schema definitions and Python executor functions for all 6 tools.

### 2a. Tool schema definitions

Each tool needs an Anthropic API-compatible schema dict:

```python
TOOL_DEFINITIONS = {
    "Read": {
        "name": "Read",
        "description": "Read a file from the filesystem and return its contents.",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "Absolute path to the file."},
                "limit":     {"type": "integer", "description": "Max lines to read."},
                "offset":    {"type": "integer", "description": "Line number to start from."},
            },
            "required": ["file_path"],
        },
    },
    "Write": {
        "name": "Write",
        "description": "Write content to a file, overwriting it if it exists.",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string"},
                "content":   {"type": "string"},
            },
            "required": ["file_path", "content"],
        },
    },
    "Edit": {
        "name": "Edit",
        "description": "Replace an exact string in a file with new content.",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path":   {"type": "string"},
                "old_string":  {"type": "string"},
                "new_string":  {"type": "string"},
                "replace_all": {"type": "boolean", "default": False},
            },
            "required": ["file_path", "old_string", "new_string"],
        },
    },
    "Glob": {
        "name": "Glob",
        "description": "Find files matching a glob pattern. Returns sorted file paths.",
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Glob pattern e.g. '**/*.py'"},
                "path":    {"type": "string", "description": "Directory to search in (default: cwd)"},
            },
            "required": ["pattern"],
        },
    },
    "Grep": {
        "name": "Grep",
        "description": "Search file contents using ripgrep or Python regex fallback.",
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string"},
                "path":    {"type": "string"},
                "glob":    {"type": "string", "description": "File filter e.g. '*.py'"},
                "-i":      {"type": "boolean", "description": "Case insensitive"},
            },
            "required": ["pattern"],
        },
    },
    "Bash": {
        "name": "Bash",
        "description": "Run a shell command and return stdout + stderr.",
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {"type": "string"},
                "timeout": {"type": "number", "description": "Timeout in seconds (default 120)"},
            },
            "required": ["command"],
        },
    },
}
```

### 2b. Tool executor functions

```python
def execute_tool(name: str, tool_input: dict, cwd: str) -> tuple[str, bool]:
    """
    Execute a tool and return (output_text, is_error).
    Security validation for Bash is handled here before subprocess execution.
    """
    try:
        if name == "Read":    return _run_read(tool_input, cwd), False
        if name == "Write":   return _run_write(tool_input, cwd), False
        if name == "Edit":    return _run_edit(tool_input, cwd), False
        if name == "Glob":    return _run_glob(tool_input, cwd), False
        if name == "Grep":    return _run_grep(tool_input, cwd), False
        if name == "Bash":    return _run_bash(tool_input, cwd), False
        return f"Unknown tool: {name}", True
    except Exception as exc:
        return f"Tool {name} failed: {exc}", True
```

**`_run_read`** — `Path.read_text()` with optional line slicing (limit/offset).

**`_run_write`** — `Path.write_text()` after creating parent dirs.

**`_run_edit`** — Load file, validate `old_string` exists exactly once (or use `replace_all`),
replace and write back. Return error string if `old_string` not found.

**`_run_glob`** — `pathlib.Path.rglob()` / `glob()`. Sort by modification time to match
Claude Code's `Glob` behavior.

**`_run_grep`** — Try `subprocess` ripgrep (`rg`) first; fall back to Python `re` scan.
Return matching file paths or `file:line:content` depending on mode.

**`_run_bash`**:
1. Call `validate_bash_command(command)` from `security.py`
2. If blocked, return the block reason as an error
3. Otherwise `asyncio.create_subprocess_shell()` with timeout (default 120s)
4. Return combined stdout + stderr

> **Note on Bash executor**: `_run_bash` must be async (subprocess). The tool executor
> dispatch must be `async def execute_tool(...)` and awaited in the loop.

---

## Step 3 — `agents/anthropic_client.py` (New)

The core of the migration: a client that runs the full agentic loop.

```python
import anthropic
from agents.types import (
    AssistantMessage, ResultMessage, RateLimitEvent, TextBlock, ToolUseBlock
)
from agents.tools import TOOL_DEFINITIONS, execute_tool

@dataclass
class AnthropicAPIClientOptions:
    system_prompt: str
    model: str           # full model ID e.g. "claude-sonnet-4-6"
    tools: list[str]     # subset of TOOL_DEFINITIONS keys
    cwd: str
    max_tokens: int = 16384
    api_key: str | None = None  # falls back to ANTHROPIC_API_KEY env var
```

### Agentic loop

```python
class AnthropicAPIClient:

    async def __aenter__(self): return self
    async def __aexit__(self, *_): pass
    async def query(self, prompt): self._task_prompt = prompt

    async def receive_response(self):  # async generator

        client = anthropic.AsyncAnthropic(api_key=self._options.api_key)
        tool_defs = [TOOL_DEFINITIONS[t] for t in self._options.tools if t in TOOL_DEFINITIONS]
        messages = [{"role": "user", "content": self._task_prompt}]

        while True:
            kwargs = dict(
                model=self._options.model,
                max_tokens=self._options.max_tokens,
                system=self._options.system_prompt,
                messages=messages,
            )
            if tool_defs:
                kwargs["tools"] = tool_defs

            response = await client.messages.create(**kwargs)

            # Build assistant message for yielding (text + tool_use blocks)
            content_blocks = []
            for block in response.content:
                if block.type == "text":
                    content_blocks.append(TextBlock(type="text", text=block.text))
                elif block.type == "tool_use":
                    content_blocks.append(ToolUseBlock(
                        type="tool_use", name=block.name, id=block.id, input=block.input
                    ))

            yield AssistantMessage(role="assistant", content=content_blocks)

            if response.stop_reason == "end_turn" or not tool_defs:
                # Store usage on self for token tracking
                self.last_usage = response.usage
                yield ResultMessage(type="result", subtype="success")
                return

            # Execute all tool_use blocks and collect results
            tool_results = []
            for block in response.content:
                if block.type != "tool_use":
                    continue
                output, is_error = await execute_tool(block.name, block.input, self._options.cwd)
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

            # Add both sides of the exchange to history and loop
            messages.append({"role": "assistant", "content": response.content})
            messages.append({"role": "user", "content": tool_results})
```

**Key design notes:**
- Uses `anthropic.AsyncAnthropic` to stay on the asyncio event loop
- Yields an `AssistantMessage` for **each assistant turn** (not just the final one) so
  `_run_agent` logs intermediate tool calls correctly
- Stores `last_usage` after the final turn for token tracking
- No `RateLimitEvent` is yielded — the existing `isinstance(message, RateLimitEvent)`
  branch in `_run_agent` simply never fires
- Tool-less agents (planner, spec-writer, spec-reviewer) get `tools=[]`, loop exits after
  first `end_turn`

---

## Step 4 — `agents/orchestrator.py`

### 4a. Replace SDK imports

**Remove:**
```python
from claude_agent_sdk import (AssistantMessage, ClaudeAgentOptions, ClaudeSDKClient,
                               ResultMessage, RateLimitEvent)
from claude_agent_sdk.types import (HookCallback, HookMatcher, TextBlock)
```

**Add:**
```python
from agents.anthropic_client import AnthropicAPIClient, AnthropicAPIClientOptions
from agents.types import AssistantMessage, ResultMessage, RateLimitEvent, TextBlock
```

`HookCallback`, `HookMatcher`, and `_BASH_HOOKS` are removed entirely — Bash security
is now enforced inside the `_run_bash` tool executor.

### 4b. Update `_make_agent_client`

Add `"anthropic"` as a third backend option alongside `"claude"` and `"codex"`.
Make `"anthropic"` the new default (replacing `"claude"`):

```python
def _make_agent_client(..., agent_type: str = "") -> AnthropicAPIClient | CodexClient:

    backend = per_agent_backend or settings.agent_backend  # default now "anthropic"

    if backend == "anthropic":
        return AnthropicAPIClient(AnthropicAPIClientOptions(
            system_prompt=system_prompt,
            model=model,      # full ID from AGENT_MODELS — already correct
            tools=tools,
            cwd=str(repo_path),
            max_tokens=settings.max_tokens_per_agent,
            api_key=settings.anthropic_api_key or None,
        ))

    if backend == "codex":
        ...  # unchanged

    # "claude" kept for backward compat — raises if CLI is unavailable
    return ClaudeSDKClient(...)
```

### 4c. Remove `_write_security_settings`

This function writes `.claude_settings.json` which is only consumed by the Claude Code
CLI. It can be deleted, along with the `self.settings_file` references in `IssueWorkflow`.
The `settings_file` parameter to `_make_agent_client` can be made optional
(`settings_file: Path | None = None`) and only used when `backend == "claude"`.

### 4d. Remove `hooks=_BASH_HOOKS` from call sites

All 4 agents that currently pass `hooks=_BASH_HOOKS` (coder, tester, reviewer,
github-submitter) no longer need it. Bash security is handled inside `_run_bash`.

### 4e. Update `_run_agent` signature

Change `client: ClaudeSDKClient` → `client: AnthropicAPIClient | CodexClient`.
The body is **unchanged** — duck typing still works.

### 4f. Token tracking integration

After `_run_agent` returns, check if the client has `last_usage`:
```python
usage = getattr(client, "last_usage", None)
# pass to record_usage() / print_usage_summary()
```

---

## Step 5 — `agents/definitions.py`

Remove SDK `AgentDefinition` import. Replace with a local dataclass:

```python
from dataclasses import dataclass

@dataclass
class AgentDefinition:
    description: str
    prompt: str
    tools: list[str]
    model: str   # full model ID now, not shortname

# Remove _shortname() — no longer needed
# Update make_*() functions to pass full model IDs
```

---

## Step 6 — `agents/codex_client.py`

Replace SDK type imports:

```python
# Remove:
from claude_agent_sdk import AssistantMessage, ResultMessage
from claude_agent_sdk.types import TextBlock

# Add:
from agents.types import AssistantMessage, ResultMessage, TextBlock
```

No other changes needed.

---

## Step 7 — `security.py`

Remove SDK imports entirely. The `validate_bash_command()` function is pure Python —
no SDK types needed. The `bash_security_hook` async function (SDK hook signature) is
deleted; security enforcement moves into `agents/tools.py::_run_bash`.

```python
# Remove these two lines:
from claude_agent_sdk import PreToolUseHookInput
from claude_agent_sdk.types import HookContext, SyncHookJSONOutput

# Remove:
async def bash_security_hook(...) -> SyncHookJSONOutput: ...

# Keep:
ALLOWED_BASE_COMMANDS, ALLOWED_GIT_SUBCOMMANDS
_extract_commands(), _validate_git_subcommand(), validate_bash_command()
```

---

## Step 8 — `token_tracker.py`

The two SDK-dependent parts:

1. **`~/.claude/stats-cache.json`** — Claude Code CLI file, no longer written.
   Remove `read_stats_cache()`, `_sum_daily_tokens()`, and the stats-cache section of
   `_print_summary()`. Replace with per-run usage from `AnthropicAPIClient.last_usage`.

2. **`RateLimitEvent` handling** — `_latest_rate_limit_events()` and the rate-limit
   section of `_print_summary()` consume SDK event objects. Remove these.

The `record_usage()` function and `UsageRecord` dataclass are **unchanged** — they
already accept a plain dict. Populate from `AnthropicAPIClient.last_usage`:

```python
# anthropic SDK response.usage fields:
usage_dict = {
    "input_tokens":                  response.usage.input_tokens,
    "output_tokens":                 response.usage.output_tokens,
    "cache_creation_input_tokens":   getattr(response.usage, "cache_creation_input_tokens", 0),
    "cache_read_input_tokens":       getattr(response.usage, "cache_read_input_tokens", 0),
}
```

A basic per-run cost estimate can be derived from token counts and known pricing.

---

## Step 9 — `config.py`

Add:
```python
# Anthropic API
anthropic_api_key: str = ""        # Falls back to ANTHROPIC_API_KEY env var if empty
max_tokens_per_agent: int = 16384  # Max output tokens per agent API call

# Update default backend
agent_backend: str = "anthropic"   # was "claude"
```

---

## Step 10 — `requirements.txt`

```
pydantic>=2.7.0
pydantic-settings>=2.3.0
anthropic>=0.40.0
python-dotenv>=1.0.1
```

Remove `claude-agent-sdk>=0.1.0`.

---

## Implementation Order

1. ~~`agents/types.py` — unblocks everything else~~ ✅
2. ~~`security.py` — remove SDK dependency, keep all logic~~ ✅
3. ~~`agents/tools.py` — tool schemas + executors (depends on security.py)~~ ✅
4. ~~`agents/anthropic_client.py` — agentic loop (depends on types + tools)~~ ✅
5. ~~`agents/codex_client.py` — swap type imports~~ ✅
6. ~~`agents/definitions.py` — local AgentDefinition~~ ✅
7. ~~`agents/orchestrator.py` — wire new client, remove hooks/settings_file~~ ✅
8. ~~`token_tracker.py` — simplify to API usage only~~ ✅
9. ~~`config.py` — add fields, change default backend~~ ✅
10. ~~`requirements.txt` — swap dependency~~ ✅

---

## Edge Cases & Gotchas

### Context window management
The agentic loop appends every turn to `messages`. Long coding sessions will eventually
hit the model's context window limit (~200K tokens for Sonnet). Add a safeguard: if
`len(messages)` grows beyond a threshold, truncate middle turns while preserving the
first user message and the last N turns.

### Edit tool — old_string uniqueness
The Claude Code `Edit` tool requires `old_string` to appear exactly once. Our Python
implementation must enforce this and return an error if not found or found multiple times
(unless `replace_all=True`). If agents currently rely on the exact error messages from
the CLI, prompt adjustments may be needed.

### Bash tool — async in sync context
`_run_bash` uses `asyncio.create_subprocess_shell`. The `execute_tool` dispatcher must
be `async def` and awaited in the `receive_response` loop.

### Model IDs — no more shortnames
`AGENT_MODELS` already stores full IDs (`"claude-sonnet-4-6"`, etc.). The `_shortname()`
conversion in `definitions.py` was only needed for the SDK's `AgentDefinition`. It should
be removed — all API calls use full IDs directly.

### `settings_file` / `_write_security_settings`
These will become dead code once the `"claude"` backend is no longer the default.
Remove them to avoid confusion. The `.claude_settings.json` file in worktrees can be
left as-is; it won't cause errors if it exists but nothing reads it.

### Codex backend still works
The Codex backend path is unaffected by this migration. It will import its message types
from `agents/types.py` after Step 6 above.

### Backward compatibility for "claude" backend
Keep the `ClaudeSDKClient` path in `_make_agent_client` behind `backend == "claude"`
in case the CLI becomes available again. It will simply raise `ImportError` on missing
SDK at runtime.

---

## Verification

1. **Unit test tools**: Manually call each tool executor with known inputs/outputs.
2. **Unit test security**: Confirm `_run_bash` blocks disallowed commands and allows
   permitted ones.
3. **Dry-run tool-less agent**: Run `_run_planner` with `AGENT_BACKEND=anthropic` — no
   tools, single API call, verify text response collected.
4. **Dry-run coder agent**: Run `_run_coder` on a trivial task — verify tool calls
   appear in logs and files are actually modified.
5. **Full issue end-to-end**: Run against a real GitHub issue with `AGENT_BACKEND=anthropic`.
6. **Token tracking**: Verify `record_usage()` is called with non-zero token counts.
7. **Codex still works**: Set `AGENT_BACKEND=codex`, confirm it still routes correctly.
