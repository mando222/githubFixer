"""
agents/tools.py — Tool definitions and executors for the Anthropic API backend.

Provides:
  TOOL_DEFINITIONS  — Anthropic API-compatible schema dict for each tool
  execute_tool()    — async dispatcher that runs the appropriate executor
"""
from __future__ import annotations

import asyncio
import re
import subprocess
from pathlib import Path

from security import validate_bash_command


# ---------------------------------------------------------------------------
# Tool schema definitions (Anthropic API format)
# ---------------------------------------------------------------------------

TOOL_DEFINITIONS: dict[str, dict] = {
    "Read": {
        "name": "Read",
        "description": (
            "Read a file from the filesystem and return its contents with line numbers. "
            "Supports optional offset (start line) and limit (max lines to return)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Absolute path to the file to read.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of lines to read.",
                },
                "offset": {
                    "type": "integer",
                    "description": "Line number (1-based) to start reading from.",
                },
            },
            "required": ["file_path"],
        },
    },
    "Write": {
        "name": "Write",
        "description": (
            "Write content to a file, overwriting it if it already exists. "
            "Creates parent directories as needed."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "Absolute path to the file."},
                "content":   {"type": "string", "description": "Full content to write."},
            },
            "required": ["file_path", "content"],
        },
    },
    "Edit": {
        "name": "Edit",
        "description": (
            "Replace an exact string in a file with new content. "
            "old_string must appear exactly once unless replace_all is true."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path":   {"type": "string"},
                "old_string":  {"type": "string", "description": "Exact text to find and replace."},
                "new_string":  {"type": "string", "description": "Replacement text."},
                "replace_all": {
                    "type": "boolean",
                    "description": "Replace all occurrences instead of requiring uniqueness.",
                    "default": False,
                },
            },
            "required": ["file_path", "old_string", "new_string"],
        },
    },
    "Glob": {
        "name": "Glob",
        "description": (
            "Find files matching a glob pattern. Returns matching file paths sorted by "
            "modification time (most recent first)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Glob pattern, e.g. '**/*.py' or 'src/**/*.ts'.",
                },
                "path": {
                    "type": "string",
                    "description": "Directory to search in. Defaults to the working directory.",
                },
            },
            "required": ["pattern"],
        },
    },
    "Grep": {
        "name": "Grep",
        "description": (
            "Search file contents for a regex pattern using ripgrep (falls back to Python re). "
            "Returns matching file paths by default, or file:line:content with output_mode='content'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Regex pattern to search for."},
                "path":    {"type": "string", "description": "File or directory to search."},
                "glob":    {"type": "string", "description": "File filter, e.g. '*.py'."},
                "-i":      {"type": "boolean", "description": "Case-insensitive search."},
                "output_mode": {
                    "type": "string",
                    "enum": ["files_with_matches", "content", "count"],
                    "description": "Output mode (default: files_with_matches).",
                },
            },
            "required": ["pattern"],
        },
    },
    "Bash": {
        "name": "Bash",
        "description": (
            "Run a shell command and return its stdout and stderr. "
            "Only permitted commands from the security allowlist may be executed."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Shell command to execute."},
                "timeout": {
                    "type": "number",
                    "description": "Timeout in seconds. Defaults to 120.",
                },
            },
            "required": ["command"],
        },
    },
}


# ---------------------------------------------------------------------------
# Async tool dispatcher
# ---------------------------------------------------------------------------

async def execute_tool(name: str, tool_input: dict, cwd: str) -> tuple[str, bool]:
    """Execute a named tool and return (output_text, is_error).

    Security enforcement for Bash is handled here before subprocess execution.
    """
    try:
        if name == "Read":
            return _run_read(tool_input, cwd), False
        if name == "Write":
            return _run_write(tool_input, cwd), False
        if name == "Edit":
            return _run_edit(tool_input, cwd), False
        if name == "Glob":
            return _run_glob(tool_input, cwd), False
        if name == "Grep":
            return _run_grep(tool_input, cwd), False
        if name == "Bash":
            return await _run_bash(tool_input, cwd)
        return f"Unknown tool: {name}", True
    except Exception as exc:
        return f"Tool {name} raised an unexpected error: {exc}", True


# ---------------------------------------------------------------------------
# Individual tool executors
# ---------------------------------------------------------------------------

def _run_read(tool_input: dict, cwd: str) -> str:
    file_path = Path(tool_input["file_path"])
    if not file_path.is_absolute():
        file_path = Path(cwd) / file_path

    if not file_path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")

    lines = file_path.read_text(encoding="utf-8", errors="replace").splitlines()

    offset: int = int(tool_input.get("offset") or 1)
    limit: int | None = tool_input.get("limit")

    # offset is 1-based
    start = max(0, offset - 1)
    end = (start + int(limit)) if limit else len(lines)
    selected = lines[start:end]

    # Format with line numbers (cat -n style)
    numbered = "\n".join(f"{start + i + 1}\t{line}" for i, line in enumerate(selected))
    return numbered


def _run_write(tool_input: dict, cwd: str) -> str:
    file_path = Path(tool_input["file_path"])
    if not file_path.is_absolute():
        file_path = Path(cwd) / file_path

    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(tool_input["content"], encoding="utf-8")
    return f"File written successfully: {file_path}"


def _run_edit(tool_input: dict, cwd: str) -> str:
    file_path = Path(tool_input["file_path"])
    if not file_path.is_absolute():
        file_path = Path(cwd) / file_path

    if not file_path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")

    old_string: str = tool_input["old_string"]
    new_string: str = tool_input["new_string"]
    replace_all: bool = bool(tool_input.get("replace_all", False))

    content = file_path.read_text(encoding="utf-8")

    if old_string not in content:
        raise ValueError(
            f"old_string not found in {file_path}. "
            "Ensure the string matches exactly (whitespace, indentation, line endings)."
        )

    if not replace_all:
        count = content.count(old_string)
        if count > 1:
            raise ValueError(
                f"old_string appears {count} times in {file_path}. "
                "Use replace_all=true to replace all occurrences, or provide more context "
                "to make old_string unique."
            )

    updated = content.replace(old_string, new_string) if replace_all else content.replace(old_string, new_string, 1)
    file_path.write_text(updated, encoding="utf-8")
    return f"File updated successfully: {file_path}"


def _run_glob(tool_input: dict, cwd: str) -> str:
    pattern: str = tool_input["pattern"]
    search_root = Path(tool_input.get("path") or cwd)
    if not search_root.is_absolute():
        search_root = Path(cwd) / search_root

    matches = list(search_root.glob(pattern))
    # Sort by modification time descending (most recent first)
    matches.sort(key=lambda p: p.stat().st_mtime, reverse=True)

    if not matches:
        return "(no files matched)"
    return "\n".join(str(p) for p in matches)


def _run_grep(tool_input: dict, cwd: str) -> str:
    pattern: str = tool_input["pattern"]
    search_path = tool_input.get("path") or cwd
    glob_filter: str | None = tool_input.get("glob")
    case_insensitive: bool = bool(tool_input.get("-i", False))
    output_mode: str = tool_input.get("output_mode", "files_with_matches")

    # Try ripgrep first (much faster)
    try:
        return _grep_rg(pattern, search_path, glob_filter, case_insensitive, output_mode, cwd)
    except FileNotFoundError:
        pass  # rg not installed, fall through

    # Python fallback
    return _grep_python(pattern, search_path, glob_filter, case_insensitive, output_mode, cwd)


def _grep_rg(
    pattern: str,
    search_path: str,
    glob_filter: str | None,
    case_insensitive: bool,
    output_mode: str,
    cwd: str,
) -> str:
    cmd = ["rg", "--no-heading"]
    if case_insensitive:
        cmd.append("-i")
    if glob_filter:
        cmd += ["--glob", glob_filter]
    if output_mode == "files_with_matches":
        cmd.append("-l")
    elif output_mode == "count":
        cmd.append("-c")
    cmd += [pattern, search_path]

    result = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)
    if result.returncode not in (0, 1):  # 1 = no matches (not an error)
        raise RuntimeError(f"rg error: {result.stderr.strip()}")
    return result.stdout.strip() or "(no matches)"


def _grep_python(
    pattern: str,
    search_path: str,
    glob_filter: str | None,
    case_insensitive: bool,
    output_mode: str,
    cwd: str,
) -> str:
    flags = re.IGNORECASE if case_insensitive else 0
    compiled = re.compile(pattern, flags)

    root = Path(search_path)
    if not root.is_absolute():
        root = Path(cwd) / root

    file_glob = glob_filter or "**/*"
    candidates = [p for p in root.glob(file_glob) if p.is_file()]

    results: list[str] = []
    counts: dict[str, int] = {}

    for filepath in candidates:
        try:
            text = filepath.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue

        if output_mode == "files_with_matches":
            if compiled.search(text):
                results.append(str(filepath))
        elif output_mode == "count":
            c = len(compiled.findall(text))
            if c:
                counts[str(filepath)] = c
        else:  # content
            for i, line in enumerate(text.splitlines(), 1):
                if compiled.search(line):
                    results.append(f"{filepath}:{i}:{line}")

    if output_mode == "count":
        if not counts:
            return "(no matches)"
        return "\n".join(f"{path}:{count}" for path, count in counts.items())

    return "\n".join(results) if results else "(no matches)"


async def _run_bash(tool_input: dict, cwd: str) -> tuple[str, bool]:
    command: str = tool_input["command"]
    timeout: float = float(tool_input.get("timeout") or 120)

    # Security validation
    allowed, reason = validate_bash_command(command)
    if not allowed:
        return (
            f"Security policy blocked this command: {reason}\nCommand: {command!r}",
            True,
        )

    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
        )
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            proc.communicate(), timeout=timeout
        )
    except asyncio.TimeoutError:
        return f"Command timed out after {timeout:.0f}s: {command!r}", True

    stdout = stdout_bytes.decode(errors="replace")
    stderr = stderr_bytes.decode(errors="replace")
    output = stdout
    if stderr.strip():
        output = output + ("\n" if output else "") + f"[stderr]\n{stderr}"
    if not output.strip():
        output = f"(exit code {proc.returncode})"
    return output, proc.returncode != 0
