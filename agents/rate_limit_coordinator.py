"""Global rate-limit coordinator for the Anthropic API.

When any agent receives a 429 (RateLimitError), it calls signal_rate_limit()
which pauses ALL concurrent agents via a shared asyncio.Event. This prevents
the thundering-herd problem where multiple agents retry simultaneously and
immediately re-trigger the same rate limit.

Usage in _run_agent():
    await wait_for_api()          # blocks if a pause is active, no-op otherwise
    ...
    except anthropic.RateLimitError as exc:
        await signal_rate_limit(delay_seconds)
"""
from __future__ import annotations

import asyncio
import logging
import time

logger = logging.getLogger(__name__)

# Lazily initialised so that importing this module before an event loop exists
# does not raise "no current event loop".
_api_ready: asyncio.Event | None = None
_pause_lock: asyncio.Lock | None = None
_pause_until: float = 0.0
_current_resume_task: asyncio.Task | None = None


def _get_event() -> asyncio.Event:
    global _api_ready
    if _api_ready is None:
        _api_ready = asyncio.Event()
        _api_ready.set()  # starts in the "available" state
    return _api_ready


def _get_lock() -> asyncio.Lock:
    global _pause_lock
    if _pause_lock is None:
        _pause_lock = asyncio.Lock()
    return _pause_lock


async def wait_for_api() -> None:
    """Wait until the API is available.

    Returns immediately (zero cost) when no pause is active.
    Blocks until the current pause expires when a 429 has been signalled.
    """
    event = _get_event()
    if not event.is_set():
        remaining = max(0.0, _pause_until - time.monotonic())
        logger.debug("Waiting %.1fs for API rate-limit pause to lift", remaining)
        await event.wait()


async def signal_rate_limit(retry_after: float) -> None:
    """Broadcast a rate-limit pause to all concurrent agents.

    Called by _run_agent when it catches an anthropic.RateLimitError.
    If a pause is already active and the new deadline is no later than the
    existing one, this is a no-op (the existing pause already covers it).
    """
    global _pause_until, _current_resume_task

    event = _get_event()
    lock = _get_lock()

    async with lock:
        deadline = time.monotonic() + retry_after
        if deadline <= _pause_until:
            # Existing pause covers it — nothing to do.
            return

        _pause_until = deadline
        event.clear()  # block all waiters

        # Cancel any shorter existing resume task and replace with this one.
        if _current_resume_task and not _current_resume_task.done():
            _current_resume_task.cancel()

        _current_resume_task = asyncio.create_task(_resume_after(retry_after))
        logger.info(
            "API rate-limit pause started — all agents will wait %.1fs before retrying",
            retry_after,
        )


async def _resume_after(delay: float) -> None:
    """Background task: sleep for delay seconds then release the pause."""
    try:
        await asyncio.sleep(delay)
        _get_event().set()
        logger.info("API rate-limit pause lifted after %.1fs — agents resuming", delay)
    except asyncio.CancelledError:
        # Replaced by a longer pause; the new task will resume instead.
        pass
