from __future__ import annotations

from config import settings

# GitHub operations use the gh CLI.
# Linear is accessed directly via the official Linear MCP server.

LINEAR_TOOLS = [
    "mcp__linear__get_issue",
    "mcp__linear__list_issues",
    "mcp__linear__save_issue",
    "mcp__linear__save_comment",
    "mcp__linear__list_issue_statuses",
    "mcp__linear__list_projects",
    "mcp__linear__get_project",
    "mcp__linear__save_project",
    "mcp__linear__list_teams",
    "mcp__linear__get_user",
]


def get_linear_mcp_config() -> dict:
    return {
        "linear": {
            "type": "http",
            "url": "https://mcp.linear.app/mcp",
            "headers": {
                "Authorization": f"Bearer {settings.linear_api_key}",
            },
        }
    }
