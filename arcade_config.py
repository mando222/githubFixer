from __future__ import annotations

from config import settings

# GitHub operations use the gh CLI (full repo scope, works for private repos).
# Arcade is only used for Linear.

ARCADE_LINEAR_TOOLS = [
    "mcp__arcade__Linear_CreateIssue",
    "mcp__arcade__Linear_UpdateIssue",
    "mcp__arcade__Linear_GetIssue",
    "mcp__arcade__Linear_ListIssues",
    "mcp__arcade__Linear_AddComment",
    "mcp__arcade__Linear_ListTeams",
    "mcp__arcade__Linear_ListWorkflowStates",
    "mcp__arcade__Linear_CreateProject",
    "mcp__arcade__Linear_GetProject",
    "mcp__arcade__Linear_ListProjects",
    "mcp__arcade__Linear_WhoAmI",
]


def get_arcade_mcp_config() -> dict:
    return {
        "arcade": {
            "type": "http",
            "url": f"https://api.arcade.dev/mcp/{settings.arcade_gateway_slug}",
            "headers": {
                "Authorization": f"Bearer {settings.arcade_api_key}",
                "Arcade-User-ID": settings.arcade_user_id,
            },
        }
    }
