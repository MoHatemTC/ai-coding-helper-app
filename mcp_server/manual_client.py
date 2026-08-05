"""Manual MCP client — run the MCP server and call tools from the terminal.

This script spawns the MCP server as a subprocess, connects via stdio,
lists available tools, and calls a selected tool.

Run with::

    uv run python -m mcp_server.manual_client

Or to call a specific tool::

    uv run python -m mcp_server.manual_client --tool web_search --query "Python 3.14"
    uv run python -m mcp_server.manual_client --tool search_code --session-id <sid> --user-id 1 --query "auth"
    uv run python -m mcp_server.manual_client --tool memory_search --user-id 1 --query "deployment"

Pass ``--mcp-user-id <id>`` to spawn the server in identity-bound mode.
"""

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import TextContent


async def main():
    """Run the manual MCP client, list tools, and call a selected tool."""
    import argparse

    parser = argparse.ArgumentParser(description="Manual MCP client for testing tools")
    parser.add_argument("--tool", default="web_search", help="Tool name to call")
    parser.add_argument("--query", default="Python 3.14", help="Query (for web_search, memory_search, search_code)")
    parser.add_argument("--question", default="Is this correct?", help="Question (for ask_human)")
    parser.add_argument("--user-id", default="1", help="User ID (for memory_search, search_code)")
    parser.add_argument("--session-id", default=None, help="Session ID (for search_code)")
    parser.add_argument("--file-name", default=None, help="Optional filename filter (for search_code)")
    parser.add_argument("--mcp-user-id", default=os.getenv("MCP_USER_ID", ""), help="Identity-bound MCP_USER_ID")
    parser.add_argument("--list-only", action="store_true", help="Just list tools, don't call any")
    args = parser.parse_args()

    # Pass the full parent environment so API keys / DB settings reach the
    # subprocess (the stdio default env only inherits a small subset on
    # Windows). MCP_USER_ID is set explicitly to toggle identity binding.
    server_env = {**os.environ, "MCP_USER_ID": args.mcp_user_id}
    server_params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "mcp_server.server"],
        cwd=Path(__file__).resolve().parent.parent,
        env=server_env,
    )

    print("Connecting to MCP server...")
    print()

    async with stdio_client(server_params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            print("Connected!\n")

            tools_result = await session.list_tools()
            tools = tools_result.tools
            print(f"Available tools ({len(tools)}):")
            for t in tools:
                print(f"  - {t.name}: {(t.description or '')[:80]}...")
            print()

            if args.list_only:
                return

            tool_name = args.tool
            print(f"Calling tool: {tool_name}")

            arguments: dict[str, Any] = {}
            if tool_name == "web_search":
                arguments = {"query": args.query}
            elif tool_name == "ask_human":
                arguments = {"question": args.question}
            elif tool_name == "memory_search":
                arguments = {"user_id": args.user_id, "query": args.query}
            elif tool_name == "search_code":
                if not args.session_id:
                    print("Error: --session-id is required for search_code")
                    return
                arguments = {
                    "query": args.query,
                    "user_id": args.user_id,
                    "session_id": args.session_id,
                    "file_name": args.file_name,
                }
            elif tool_name == "server_status":
                arguments = {}
            else:
                print(f"Unknown tool: {tool_name}")
                print("Available tools: server_status, web_search, search_code, memory_search, ask_human")
                return

            print(f"Arguments: {json.dumps(arguments, indent=2)}")
            print()

            result = await session.call_tool(tool_name, arguments)

            print("Result:")
            for content in result.content:
                if isinstance(content, TextContent):
                    try:
                        parsed = json.loads(content.text)
                        print(json.dumps(parsed, indent=2))
                    except (json.JSONDecodeError, TypeError):
                        print(content.text)
                else:
                    print(str(content))

            if result.isError:
                print("\n[Tool returned an error]")


if __name__ == "__main__":
    asyncio.run(main())
