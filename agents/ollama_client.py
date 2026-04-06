"""
agents/ollama_client.py

Minimal async client for the Ollama OpenAI-compatible chat completions endpoint.
Used only for tool-free agents (spec_reviewer, planner) as a zero-cost alternative
to Claude API calls. Agents that require tool use (coder, tester, etc.) cannot use
this client and must continue using the Claude SDK.
"""
from __future__ import annotations

import logging
import time

import httpx

from config import settings

logger = logging.getLogger(__name__)

# Short connect timeout so we fail fast if Ollama is not running,
# but a long read timeout to handle slow local inference.
_OLLAMA_TIMEOUT = httpx.Timeout(connect=5.0, read=300.0, write=10.0, pool=5.0)


class OllamaUnavailableError(RuntimeError):
    """Ollama server is not reachable. Caller should fall back to Claude."""


class OllamaResponseError(RuntimeError):
    """Ollama returned a bad or empty response."""


async def run_ollama_agent(
    system_prompt: str,
    task_prompt: str,
    model: str | None = None,
    label: str = "",
) -> str:
    """
    Call Ollama's /v1/chat/completions and return the assistant text.

    Raises OllamaUnavailableError if Ollama is not reachable (connect/timeout).
    Raises OllamaResponseError if the response is malformed or empty.
    """
    resolved_model = model or settings.ollama_model
    url = f"{settings.ollama_base_url}/v1/chat/completions"
    payload = {
        "model": resolved_model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": task_prompt},
        ],
        "stream": False,
        "temperature": 0.1,  # low temp for deterministic structured/classification output
    }

    label_prefix = f"[{label}]" if label else "[ollama]"
    start = time.monotonic()

    try:
        async with httpx.AsyncClient(timeout=_OLLAMA_TIMEOUT) as client:
            logger.info("%s Calling Ollama model=%s url=%s", label_prefix, resolved_model, url)
            response = await client.post(url, json=payload)
    except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
        raise OllamaUnavailableError(
            f"Ollama not reachable at {settings.ollama_base_url}: {exc}"
        ) from exc
    except httpx.ReadTimeout as exc:
        raise OllamaResponseError(f"Ollama read timeout after 300s: {exc}") from exc

    elapsed = time.monotonic() - start

    if response.status_code != 200:
        raise OllamaResponseError(
            f"Ollama returned HTTP {response.status_code}: {response.text[:200]}"
        )

    try:
        data = response.json()
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, ValueError) as exc:
        raise OllamaResponseError(
            f"Unexpected Ollama response structure: {exc}\nBody: {response.text[:500]}"
        ) from exc

    content = content.strip()
    if not content:
        raise OllamaResponseError("Ollama returned an empty response")

    logger.info(
        "%s Ollama response received model=%s elapsed=%.1fs chars=%d",
        label_prefix, resolved_model, elapsed, len(content),
    )
    return content
