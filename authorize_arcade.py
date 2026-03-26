#!/usr/bin/env python3
"""
Authorize Arcade Services
=========================

Run this script once to connect your GitHub and Linear accounts via OAuth
before starting the issue-solver server.

Usage:
    python authorize_arcade.py           # Authorize all services
    python authorize_arcade.py github    # Authorize GitHub only
    python authorize_arcade.py linear    # Authorize Linear only
"""

import sys
import traceback
import os

from dotenv import load_dotenv

load_dotenv()

from arcadepy import Arcade  # pip install arcadepy

# Tools that require write authorization per service.
# Arcade tool names follow the pattern: Provider_ToolName
# NOTE: Tool names passed to client.tools.authorize() use dot notation with
# the provider name exactly as Arcade registers it (e.g. "Github", not "GitHub").
# These differ from the MCP tool names used inside agents (mcp__arcade__GitHub_*).
# GitHub operations now use the gh CLI (full repo scope, works for private repos).
# Arcade is only used for Linear.
SERVICES = {
    "linear": {
        "verify_tool": "Linear.WhoAmI",
        "extract_name": lambda o: o.get("name", str(o)),
        "auth_tools": [
            "Linear.CreateProject",
            "Linear.CreateIssue",
            "Linear.UpdateIssue",
            "Linear.AddComment",
            "Linear.TransitionIssueState",
        ],
    },
}


def authorize_service(client: Arcade, user_id: str, service: str) -> bool:
    """Authorize all write tools for a service. Returns True if all succeeded."""
    config = SERVICES[service]
    auth_tools = config["auth_tools"]
    verify_tool = config["verify_tool"]
    extract_name = config["extract_name"]

    print(f"\n{'='*60}")
    print(f"  {service.upper()} ({len(auth_tools)} tools to authorize)")
    print(f"{'='*60}")

    all_authorized = True

    for i, auth_tool in enumerate(auth_tools, 1):
        print(f"\n[{i}/{len(auth_tools)}] Authorizing: {auth_tool}")

        auth_response = client.tools.authorize(
            tool_name=auth_tool,
            user_id=user_id,
        )

        if auth_response.status == "completed":
            print("  Already authorized")
        else:
            print("  Authorization required. Click this link:\n")
            print(f"    {auth_response.url}")
            print("\n  Waiting for authorization...")

            try:
                if auth_response.id is None:
                    print("  Error: No authorization ID returned")
                    all_authorized = False
                    continue
                client.auth.wait_for_completion(auth_response.id)
                print("  Authorized!")
            except KeyboardInterrupt:
                print(f"\n\nAuthorization interrupted.")
                print(f"Stopped at: {auth_tool}")
                print(f"\nTo resume, run: python authorize_arcade.py {service}")
                raise

    # Verify connection
    if all_authorized:
        print(f"\nVerifying {service.title()} connection...")
        try:
            result = client.tools.execute(
                tool_name=verify_tool,
                input={},
                user_id=user_id,
            )
            output = result.output.value if result.output else None
            name = extract_name(output) if isinstance(output, dict) else str(output)
            print(f"Connected as: {name}")
        except ConnectionError as e:
            print(f"Verification failed (network error): {e}")
            all_authorized = False
        except Exception as e:
            print(f"Verification failed ({type(e).__name__}): {e}")
            traceback.print_exc()
            print("\nThis may indicate invalid credentials or an expired OAuth token.")
            print("Check your gateway at: https://api.arcade.dev/dashboard/mcp-gateways")
            all_authorized = False

    return all_authorized


def main() -> None:
    api_key = os.environ.get("ARCADE_API_KEY")
    user_id = os.environ.get("ARCADE_USER_ID", "agent@local")

    if not api_key:
        print("Error: ARCADE_API_KEY not set in .env")
        sys.exit(1)

    if len(sys.argv) > 1:
        requested = [s.lower() for s in sys.argv[1:]]
        services = [s for s in requested if s in SERVICES]
        unknown = [s for s in requested if s not in SERVICES]
        if unknown:
            print(f"Unknown service(s): {', '.join(unknown)}")
            print(f"Available: {', '.join(SERVICES.keys())}")
            sys.exit(1)
    else:
        services = list(SERVICES.keys())

    print("Arcade Service Authorization — GitHub Issue Solver")
    print(f"User:     {user_id}")
    print(f"API Key:  {api_key[:20]}...")
    print(f"Services: {', '.join(services)}")

    client = Arcade(api_key=api_key)

    results = {}
    for service in services:
        try:
            results[service] = authorize_service(client, user_id, service)
        except KeyboardInterrupt:
            sys.exit(1)

    # Summary
    print(f"\n{'='*60}")
    print("  AUTHORIZATION SUMMARY")
    print(f"{'='*60}")
    total_tools = 0
    for service, success in results.items():
        tool_count = len(SERVICES[service]["auth_tools"])
        total_tools += tool_count
        status = f"OK ({tool_count} tools)" if success else f"INCOMPLETE ({tool_count} tools)"
        print(f"  {service.title()}: {status}")

    print(f"\n  Total: {total_tools} write tools across {len(results)} services")

    if all(results.values()):
        print("\n  All authorizations complete! You can now start the server.")
        sys.exit(0)
    else:
        print("\n  Some authorizations incomplete. Re-run to finish.")
        sys.exit(1)


if __name__ == "__main__":
    main()
