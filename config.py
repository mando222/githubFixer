from __future__ import annotations

from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Linear — find these in Linear > Settings > API
    linear_api_key: str  # Linear > Settings > API > Personal API Keys
    linear_team_id: str
    linear_in_progress_state_id: str = ""
    linear_in_review_state_id: str = ""
    linear_done_state_id: str = ""
    linear_needs_clarification_state_id: str = ""

    # Models (optional — defaults shown)
    orchestrator_model: str = "claude-sonnet-4-6"
    coding_agent_model: str = "claude-sonnet-4-6"
    tester_agent_model: str = "claude-sonnet-4-6"
    reviewer_agent_model: str = "claude-sonnet-4-6"
    github_agent_model: str = "claude-haiku-4-5-20251001"
    analyzer_agent_model: str = "claude-haiku-4-5-20251001"
    planner_agent_model: str = "claude-haiku-4-5-20251001"
    spec_writer_agent_model: str = "claude-sonnet-4-6"  # override to claude-opus-4-6 via .env
    spec_reviewer_agent_model: str = "claude-haiku-4-5-20251001"  # pure comparison, no tools

    # Concurrency — direct Linear API supports concurrent connections
    max_concurrent_planners: int = 5   # planning has no semaphore by default; cap at 5 concurrent
    max_concurrent_issues: int = 3
    # Testers can run more broadly in parallel than coders (I/O-bound, not CPU-bound)
    max_concurrent_testers: int = 5

    # How long (seconds) to allow a single issue workflow before timing out
    issue_timeout_seconds: int = 1800  # 30 minutes

    # How long (seconds) to allow planning phases (0.5-4) before timing out
    planning_timeout_seconds: int = 900  # 15 minutes (increased for spec writer + reviewer phases)

    # GitHub bot login used to filter out bot comments when detecting user replies
    github_bot_login: str = "github-actions[bot]"

    # Max code→test→fix cycles before blocking
    max_remediation_cycles: int = 3
    # Max review-fix-re-review cycles before blocking
    max_review_cycles: int = 2
    # Max fix tasks spawned per review cycle (prevents coder explosion when reviewer lists many issues)
    max_fix_tasks_per_review_cycle: int = 3

    # ── Anthropic API ─────────────────────────────────────────────────────
    # API key for direct Anthropic API access (the default backend).
    # Falls back to the ANTHROPIC_API_KEY environment variable if empty.
    anthropic_api_key: str = ""

    # Max output tokens per individual agent API call.
    max_tokens_per_agent: int = 16384

    # ── Agent backend ──────────────────────────────────────────────────────
    # "anthropic" — direct Anthropic API (default, no CLI required)
    # "codex"     — OpenAI Codex CLI (requires: npm install -g @openai/codex)
    agent_backend: str = "anthropic"  # "anthropic" | "codex"

    # Per-agent backend overrides (empty string → use agent_backend above).
    # Example .env entry:  CODER_AGENT_BACKEND=codex
    analyzer_agent_backend: str = ""
    coder_agent_backend: str = ""
    tester_agent_backend: str = ""
    reviewer_agent_backend: str = ""
    github_agent_backend: str = ""
    planner_agent_backend: str = ""
    spec_writer_agent_backend: str = ""
    spec_reviewer_agent_backend: str = ""

    # Codex model names — one per agent type.
    # OpenAI model IDs: o4-mini, o3, gpt-4.1, etc.
    codex_analyzer_model: str = "o4-mini"
    codex_coder_model: str = "o4-mini"
    codex_tester_model: str = "o4-mini"
    codex_reviewer_model: str = "o4-mini"
    codex_github_model: str = "o4-mini"
    codex_planner_model: str = "o4-mini"
    codex_spec_writer_model: str = "o4-mini"
    codex_spec_reviewer_model: str = "o4-mini"

    # Per-call subprocess timeout for Codex agents (Codex is slower than SDK streaming).
    codex_timeout_seconds: int = 600  # 10 minutes

    # Ollama — optional local LLM for tool-free agents (zero API cost)
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "qwen2.5:14b"
    ollama_for_planner: bool = False        # env: USE_OLLAMA_FOR_PLANNER=true
    ollama_for_spec_reviewer: bool = False  # env: USE_OLLAMA_FOR_SPEC_REVIEWER=true

    # mempalace — optional persistent cross-run memory
    # Requires: pip install git+https://github.com/milla-jovovich/mempalace
    # Then run: mempalace init ~/.mempalace
    mempalace_enabled: bool = False
    mempalace_palace_path: str = "~/.mempalace/palace"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
