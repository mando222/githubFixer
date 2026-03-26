from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class Task:
    """A single implementation task returned by the planner agent."""
    title: str
    description: str
    files_hint: list[str] = field(default_factory=list)
    acceptance: str = ""


@dataclass
class IssueEvent:
    """Represents a GitHub issue to be solved.

    Can be constructed from a raw GitHub REST API issue object
    (via ``from_api``) or from a legacy webhook payload (``from_payload``).
    """

    number: int
    title: str
    body: str
    repo_full_name: str   # "owner/repo"
    repo_name: str        # "repo"
    repo_owner: str       # "owner"
    clone_url: str
    html_url: str         # issue URL
    repo_html_url: str    # repo URL

    # ------------------------------------------------------------------ #
    # Constructors                                                         #
    # ------------------------------------------------------------------ #

    @classmethod
    def from_api(cls, issue: dict, repo: dict) -> "IssueEvent":
        """Build from gh CLI output objects.

        ``issue`` is a single item from ``gh issue list/view --json``.
        ``repo``  is the locally-constructed dict with full_name/clone_url/html_url.

        gh CLI uses ``url`` for the issue URL (not ``html_url``).
        """
        full_name: str = repo["full_name"]
        owner_login, repo_name = full_name.split("/", 1)
        repo_html_url = repo.get("html_url") or f"https://github.com/{full_name}"
        issue_url = issue.get("url") or issue.get("html_url") or ""

        return cls(
            number=issue["number"],
            title=issue["title"],
            body=issue.get("body") or "",
            repo_full_name=full_name,
            repo_name=repo_name,
            repo_owner=owner_login,
            clone_url=repo["clone_url"],
            html_url=issue_url,
            repo_html_url=repo_html_url,
        )

    @classmethod
    def from_payload(cls, payload: dict) -> "IssueEvent":
        """Build from a GitHub webhook ``issues`` event payload (legacy)."""
        issue = payload["issue"]
        repo = payload["repository"]
        return cls(
            number=issue["number"],
            title=issue["title"],
            body=issue.get("body") or "",
            repo_full_name=repo["full_name"],
            repo_name=repo["name"],
            repo_owner=repo["owner"]["login"],
            clone_url=repo["clone_url"],
            html_url=issue["html_url"],
            repo_html_url=repo["html_url"],
        )

    # ------------------------------------------------------------------ #
    # Derived properties                                                   #
    # ------------------------------------------------------------------ #

    @property
    def branch_slug(self) -> str:
        """URL-safe slug for the issue title (max 40 chars)."""
        slug = re.sub(r"[^a-z0-9]+", "-", self.title.lower())
        slug = slug.strip("-")[:40].rstrip("-")
        return slug or "fix"

    @property
    def branch_name(self) -> str:
        return f"fix/issue-{self.number}-{self.branch_slug}"

    def __str__(self) -> str:
        return f"#{self.number}: {self.title}"
