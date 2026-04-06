"""
token_tracker.py — Token usage tracking for github-fixer.

Writes per-issue JSONL records to ~/.github-fixer/token_usage.jsonl using
usage data returned directly by the Anthropic API (response.usage).
"""
from __future__ import annotations

import json
import logging
import threading
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

TRACKER_DIR = Path.home() / ".github-fixer"
USAGE_FILE = TRACKER_DIR / "token_usage.jsonl"

_write_lock = threading.Lock()

SEP = "─" * 80


# --------------------------------------------------------------------------- #
# Per-issue JSONL record                                                        #
# --------------------------------------------------------------------------- #

@dataclass
class UsageRecord:
    timestamp: str
    issue_ref: str
    input_tokens: int
    output_tokens: int
    cache_creation_tokens: int
    cache_read_tokens: int
    cost_usd: float


def record_usage(
    issue_ref: str,
    usage: dict | None,
    cost_usd: float | None,
) -> None:
    """Append one per-issue usage record to USAGE_FILE. Never raises.

    The ``usage`` dict should contain the fields from anthropic.types.Usage:
      input_tokens, output_tokens,
      cache_creation_input_tokens (optional),
      cache_read_input_tokens (optional).
    """
    try:
        record = UsageRecord(
            timestamp=datetime.now(timezone.utc).isoformat(),
            issue_ref=issue_ref,
            input_tokens=int((usage or {}).get("input_tokens", 0)),
            output_tokens=int((usage or {}).get("output_tokens", 0)),
            cache_creation_tokens=int((usage or {}).get("cache_creation_input_tokens", 0)),
            cache_read_tokens=int((usage or {}).get("cache_read_input_tokens", 0)),
            cost_usd=float(cost_usd or 0.0),
        )
        TRACKER_DIR.mkdir(parents=True, exist_ok=True)
        line = json.dumps(asdict(record)) + "\n"
        with _write_lock:
            with USAGE_FILE.open("a", encoding="utf-8") as f:
                f.write(line)
    except Exception:
        logger.warning("Failed to record token usage", exc_info=True)


# --------------------------------------------------------------------------- #
# Usage summary printer                                                         #
# --------------------------------------------------------------------------- #

def print_usage_summary(
    issue_ref: str = "",
    last_usage: dict | None = None,
    last_cost: float | None = None,
) -> None:
    """Print a brief token usage summary to stdout. Never raises."""
    try:
        _print_summary(issue_ref, last_usage, last_cost)
    except Exception:
        logger.warning("Failed to print token usage summary", exc_info=True)


def _print_summary(
    issue_ref: str,
    last_usage: dict | None,
    last_cost: float | None,
) -> None:
    header = "  Anthropic API Usage Summary"
    if issue_ref:
        header += f"  ({issue_ref}"
        if last_usage:
            inp = last_usage.get("input_tokens", 0)
            out = last_usage.get("output_tokens", 0)
            cache_read = last_usage.get("cache_read_input_tokens", 0)
            cache_create = last_usage.get("cache_creation_input_tokens", 0)
            header += f" — this run: {inp:,} in / {out:,} out"
            if cache_read or cache_create:
                header += f" / {cache_read:,} cache-read / {cache_create:,} cache-create"
            if last_cost:
                header += f" / ${last_cost:.4f}"
        header += ")"

    print(SEP)
    print(header)
    print(SEP)

    # Show cumulative history from the JSONL log
    if USAGE_FILE.exists():
        try:
            records = [
                json.loads(line)
                for line in USAGE_FILE.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            if records:
                total_in = sum(r.get("input_tokens", 0) for r in records)
                total_out = sum(r.get("output_tokens", 0) for r in records)
                total_cost = sum(r.get("cost_usd", 0.0) for r in records)
                print(f"  Cumulative ({len(records)} runs logged):")
                print(f"    {total_in:>14,} input tokens")
                print(f"    {total_out:>14,} output tokens")
                if total_cost:
                    print(f"    ${total_cost:.4f} estimated cost")
        except Exception:
            pass
    else:
        print("  (no usage history — token_usage.jsonl not found)")

    print(SEP)
