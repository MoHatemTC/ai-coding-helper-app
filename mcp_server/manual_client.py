"""Manual MCP client — run the MCP server and call tools from the terminal.

This script spawns the MCP server as a subprocess, connects via stdio,
lists available tools, and calls the review_code tool as a demo.

Run with::

    uv run python -m mcp_server.manual_client

Or to call a specific tool::

    uv run python -m mcp_server.manual_client --tool review_code --code "x = 1 / 0"
"""

import asyncio
import json
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main():
    """Run the manual MCP client, list tools, and call a selected tool."""
    import argparse

    parser = argparse.ArgumentParser(description="Manual MCP client for testing tools")
    parser.add_argument("--tool", default="review_code", help="Tool name to call")
    parser.add_argument("--code", default="x = 1 / 0", help="Code to review (for review_code)")
    parser.add_argument("--query", default="Python 3.14", help="Query (for web_search)")
    parser.add_argument("--question", default="Is this correct?", help="Question (for ask_human)")
    parser.add_argument("--list-only", action="store_true", help="Just list tools, don't call any")
    args = parser.parse_args()

    server_params = StdioServerParameters(
        command="uv",
        args=[
            "run",
            "--project",
            "d:\\Programming\\Sprints\\ai-coding-helper-app",
            "python",
            "-m",
            "mcp_server.server",
        ],
        env=None,
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
                print(f"  - {t.name}: {t.description[:80]}...")
            print()

            if args.list_only:
                return

            tool_name = args.tool
            print(f"Calling tool: {tool_name}")

            arguments: dict[str, Any] = {}
            if tool_name == "review_code":
                arguments = {"code": args.code, "language": "python"}
            elif tool_name == "web_search":
                arguments = {"query": args.query}
            elif tool_name == "ask_human":
                arguments = {"question": args.question}
            elif tool_name == "memory_search":
                arguments = {"user_id": "test-user", "query": args.query}
            elif tool_name == "memory_add":
                arguments = {"user_id": "test-user", "content": "Test memory entry"}
            elif tool_name == "get_audit_log":
                arguments = {"limit": 10}
            else:
                print(f"Unknown tool: {tool_name}")
                return

            print(f"Arguments: {json.dumps(arguments, indent=2)}")
            print()

            result = await session.call_tool(tool_name, arguments)

            print("Result:")
            for content in result.content:
                if hasattr(content, "text"):
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
