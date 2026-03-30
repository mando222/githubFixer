from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class IssueEvent:
    """Represents a GitHub issue to be solved."""

    number: int
    title: str
    body: str
    repo_full_name: str   # "owner/repo"
    repo_name: str        # "repo"
    repo_owner: str       # "owner"
    clone_url: str
    html_url: str         # issue URL
    repo_html_url: str    # repo URL
    force: bool = False   # bypass won't-implement / Cancelled state checks

    # ------------------------------------------------------------------ #
    # Constructors                                                         #
    # ------------------------------------------------------------------ #

    @classmethod
    def from_api(cls, issue: dict, repo: dict, *, force: bool = False) -> "IssueEvent":
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
            force=force,
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
