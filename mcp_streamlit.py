r"""Interactive Streamlit client for the local MCP server.

Run from the project root with::

    .venv\\Scripts\\python.exe -m streamlit run mcp_streamlit.py

The app starts the real ``mcp_server.server`` as a subprocess, connects to it
with the MCP Python client over stdio, discovers its tools, and calls the
selected tool. It is intentionally a client demo rather than a mock so the
screen shows an actual MCP round trip.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import streamlit as st
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


PROJECT_ROOT = Path(__file__).resolve().parent


def build_arguments(tool_name: str, values: dict[str, str]) -> dict[str, str]:
    """Build the MCP tool arguments from the visible form fields."""
    if tool_name == "server_status":
        return {}
    if tool_name == "memory_search":
        return {"user_id": values["user_id"], "query": values["query"]}
    if tool_name == "ask_human":
        return {"question": values["question"]}
    return {"query": values["query"]}


def content_to_json(content: object) -> dict[str, Any] | str:
    """Convert an MCP content block into a displayable JSON value."""
    if hasattr(content, "model_dump"):
        return content.model_dump(mode="json", by_alias=True, exclude_none=True)
    if hasattr(content, "text"):
        return str(content.text)
    return str(content)


async def call_mcp_tool(tool_name: str, arguments: dict[str, str]) -> dict[str, Any]:
    """Connect to the real local MCP server, inspect it, and call one tool."""
    server_parameters = StdioServerParameters(
        command=sys.executable,
        args=["-m", "mcp_server.server"],
        cwd=PROJECT_ROOT,
    )

    async with stdio_client(server_parameters) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            tools_result = await session.list_tools()
            discovered_tools = [
                {
                    "name": tool.name,
                    "description": tool.description or "",
                    "input_schema": getattr(tool, "inputSchema", {}),
                }
                for tool in tools_result.tools
            ]
            result = await session.call_tool(tool_name, arguments)

            return {
                "server": "ai-coding-helper-tools",
                "transport": "stdio",
                "tools": discovered_tools,
                "request": {"method": "tools/call", "params": {"name": tool_name, "arguments": arguments}},
                "response": {
                    "is_error": bool(result.isError),
                    "content": [content_to_json(item) for item in result.content],
                    "structured_content": getattr(result, "structuredContent", None),
                },
            }


def run_mcp_tool(tool_name: str, arguments: dict[str, str]) -> dict[str, Any]:
    """Run the async MCP client from Streamlit's synchronous script."""
    return asyncio.run(call_mcp_tool(tool_name, arguments))


st.set_page_config(page_title="MCP Live Demonstration", page_icon="🔌", layout="wide")

st.title("🔌 MCP live demonstration")
st.caption("A real MCP client calling the local AI Coding Helper MCP server over stdio")

with st.sidebar:
    st.header("Tool input")
    tool_name = st.selectbox(
        "Choose an MCP tool",
        ["server_status", "memory_search", "web_search", "ask_human"],
        help="These names are discovered from mcp_server.server when you click Run MCP call.",
    )

    user_id = "demo-student"
    query = "What Python performance topics has this user explored?"
    question = "Should the agent continue with this plan?"

    if tool_name == "memory_search":
        user_id = st.text_input("user_id", value=user_id)
        query = st.text_input("query", value=query)
    elif tool_name == "web_search":
        query = st.text_input("query", value="Python async best practices")
    else:
        question = st.text_area("question", value=question)

    values = {"user_id": user_id, "query": query, "question": question}
    arguments = build_arguments(tool_name, values)
    run_call = st.button("Run MCP call", type="primary", use_container_width=True)

    st.info(
    "What this proves: Streamlit is acting as an MCP client, discovering the server's tools, "
    "sending structured arguments, and rendering the server response."
)

if run_call:
    if any(not value.strip() for value in arguments.values()):
        st.warning("Please fill in every input field before running the call.")
    else:
        with st.spinner("Connecting to MCP server and calling the selected tool..."):
            try:
                result = run_mcp_tool(tool_name, arguments)
            except Exception as exc:
                st.error("The MCP client could not complete the round trip.")
                st.exception(exc)
            else:
                discovered_tools = result["tools"]
                response = result["response"]

                metric_columns = st.columns(4)
                metric_columns[0].metric("Connection", "✅ Connected")
                metric_columns[1].metric("Transport", result["transport"])
                metric_columns[2].metric("Tools found", len(discovered_tools))
                metric_columns[3].metric("Tool status", "❌ Error" if response["is_error"] else "✅ Success")

                st.subheader("1. Tools discovered from the MCP server")
                st.dataframe(
                    [
                        {"name": item["name"], "description": item["description"]}
                        for item in discovered_tools
                    ],
                    use_container_width=True,
                    hide_index=True,
                )

                left, right = st.columns(2)
                with left:
                    st.subheader("2. Input sent to MCP")
                    st.code(json.dumps(result["request"], indent=2), language="json")
                with right:
                    st.subheader("3. Output received from MCP")
                    st.json(response)

                if response["is_error"]:
                    st.warning(
                        "The MCP protocol round trip completed, but the selected tool returned an error. "
                        "For web_search, check that TAVILY_API_KEY is configured."
                    )
                else:
                    st.success(f"MCP call to `{tool_name}` completed successfully.")

                with st.expander("Show complete response payload"):
                    st.json(result)

else:
    st.subheader("How to present this")
    st.markdown(
        "1. Keep **server_status** selected and click **Run MCP call**.\n"
        "2. Point out the `tools/list` discovery result and the four available tools.\n"
        "3. Compare the JSON input on the left with the MCP response on the right.\n"
        "4. Try **web_search** afterward if `TAVILY_API_KEY` is configured."
    )
