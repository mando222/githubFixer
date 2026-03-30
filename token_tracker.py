"""
token_tracker.py — Token usage tracking for github-fixer.

Pulls data from two sources:
  1. ~/.claude/stats-cache.json  — Claude Code's own token accounting (daily + cumulative)
  2. RateLimitEvent messages     — Claude's live rate-limit utilization per window type
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

STATS_CACHE = Path.home() / ".claude" / "stats-cache.json"

SEP = "─" * 80


# --------------------------------------------------------------------------- #
# Stats cache reader (Claude Code's own tracking)                               #
# --------------------------------------------------------------------------- #

def read_stats_cache() -> dict | None:
    """Read ~/.claude/stats-cache.json. Returns None if unavailable."""
    try:
        return json.loads(STATS_CACHE.read_text(encoding="utf-8"))
    except Exception:
        return None


def _sum_daily_tokens(daily_model_tokens: list[dict], since_date: str) -> dict[str, int]:
    """
    Sum tokensByModel for all dates >= since_date (YYYY-MM-DD string comparison).
    Returns {model_name: token_count}.
    """
    totals: dict[str, int] = {}
    for entry in daily_model_tokens:
        if entry.get("date", "") >= since_date:
            for model, count in entry.get("tokensByModel", {}).items():
                totals[model] = totals.get(model, 0) + int(count)
    return totals


def _today_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _days_ago_str(n: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=n)).strftime("%Y-%m-%d")


# --------------------------------------------------------------------------- #
# Rate limit event helpers                                                      #
# --------------------------------------------------------------------------- #

_WINDOW_LABELS = {
    "five_hour":        "5-hour",
    "seven_day":        "7-day",
    "seven_day_opus":   "7-day (opus)",
    "seven_day_sonnet": "7-day (sonnet)",
    "overage":          "Overage",
}


def _format_resets_in(resets_at: int | None) -> str:
    """Format a Unix timestamp as 'in Xd Yh Zm' relative to now."""
    if resets_at is None:
        return "unknown"
    now = datetime.now(timezone.utc).timestamp()
    delta_s = int(resets_at - now)
    if delta_s <= 0:
        return "soon"
    days, rem = divmod(delta_s, 86400)
    hours, rem = divmod(rem, 3600)
    mins = rem // 60
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if mins or not parts:
        parts.append(f"{mins}m")
    return "in " + " ".join(parts)


def _latest_rate_limit_events(rate_limit_events: list) -> dict[str, object]:
    """
    Deduplicate rate limit events, keeping the latest per rate_limit_type.
    Returns {rate_limit_type: RateLimitEvent}.
    """
    latest: dict[str, object] = {}
    for event in (rate_limit_events or []):
        try:
            rl_type = event.rate_limit_info.rate_limit_type
            latest[rl_type] = event
        except Exception:
            pass
    return latest


# --------------------------------------------------------------------------- #
# Main summary printer                                                          #
# --------------------------------------------------------------------------- #

def print_usage_summary(
    issue_ref: str = "",
    last_usage: dict | None = None,
    last_cost: float | None = None,
    rate_limit_events: list | None = None,
) -> None:
    """Print a Claude Code usage summary to stdout. Never raises."""
    try:
        _print_summary(issue_ref, last_usage, last_cost, rate_limit_events or [])
    except Exception:
        logger.warning("Failed to print token usage summary", exc_info=True)


def _print_summary(
    issue_ref: str,
    last_usage: dict | None,
    last_cost: float | None,
    rate_limit_events: list,
) -> None:
    # ── Header ──────────────────────────────────────────────────────────────
    header = "  Claude Code Usage Summary"
    if issue_ref:
        header += f"  ({issue_ref}"
        if last_usage:
            inp = last_usage.get("input_tokens", 0)
            out = last_usage.get("output_tokens", 0)
            header += f" — this run: {inp:,} in / {out:,} out"
            if last_cost:
                header += f" / ${last_cost:.4f}"
        header += ")"

    print(SEP)
    print(header)
    print(SEP)

    # ── Token usage from stats-cache ─────────────────────────────────────────
    cache = read_stats_cache()
    if cache:
        daily_tokens = cache.get("dailyModelTokens", [])
        model_usage = cache.get("modelUsage", {})

        today = _today_str()
        day7 = _days_ago_str(7)
        day30 = _days_ago_str(30)

        today_totals = _sum_daily_tokens(daily_tokens, today)
        week_totals = _sum_daily_tokens(daily_tokens, day7)
        month_totals = _sum_daily_tokens(daily_tokens, day30)

        print("  Token Usage (from Claude Code stats)")
        print(f"  {'Period':<16} {'Tokens':>14}  Models")
        print(f"  {'-'*16} {'-'*14}  {'-'*30}")

        def _row(label: str, totals: dict[str, int]) -> None:
            if not totals:
                print(f"  {label:<16} {'—':>14}")
                return
            models_str = ", ".join(totals.keys())
            total = sum(totals.values())
            print(f"  {label:<16} {total:>14,}  {models_str}")

        _row("Today", today_totals)
        _row("Last 7 days", week_totals)
        _row("Last 30 days", month_totals)

        if model_usage:
            print()
            print("  Cumulative (all time):")
            for model, stats in model_usage.items():
                inp = stats.get("inputTokens", 0)
                out = stats.get("outputTokens", 0)
                cache_read = stats.get("cacheReadInputTokens", 0)
                cache_cr = stats.get("cacheCreationInputTokens", 0)
                print(f"    {model}:")
                print(f"      {inp:>14,} input  /  {out:>14,} output")
                if cache_read or cache_cr:
                    print(f"      {cache_read:>14,} cache-read  /  {cache_cr:>14,} cache-create")
    else:
        print("  (stats-cache.json unavailable — token history not shown)")

    # ── Rate limit status ────────────────────────────────────────────────────
    latest = _latest_rate_limit_events(rate_limit_events)
    if latest:
        print()
        print(SEP)
        print("  Rate Limit Status (from Claude Code)")
        print(f"  {'Window':<20} {'Utilization':>12}  {'Status':<18}  Resets")
        print(f"  {'-'*20} {'-'*12}  {'-'*18}  {'-'*14}")
        for rl_type, event in sorted(latest.items()):
            try:
                info = event.rate_limit_info
                label = _WINDOW_LABELS.get(rl_type, rl_type)
                utilization = info.utilization
                pct = f"{utilization * 100:.1f}%" if utilization is not None else "?"
                status = getattr(info, "status", "?")
                resets = _format_resets_in(getattr(info, "resets_at", None))
                remaining_pct = f"  ({(1 - utilization) * 100:.1f}% remaining)" if utilization is not None else ""
                print(f"  {label:<20} {pct:>12}  {status:<18}  {resets}{remaining_pct}")
            except Exception:
                pass

    print(SEP)
