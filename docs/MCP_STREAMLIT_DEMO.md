# MCP Streamlit Demonstration

Run this from the project root:

```powershell
.venv\Scripts\python.exe -m streamlit run mcp_streamlit.py
```

The page is a real MCP client. It starts `mcp_server.server` as a subprocess,
connects over stdio, initializes the MCP session, calls `tools/list`, and then
calls the selected tool with the form values.

For a reliable classroom demonstration, leave `server_status` selected. It
shows a successful, dependency-free MCP round trip. The `memory_search` option
demonstrates the memory tool, while `web_search` uses Tavily when
`TAVILY_API_KEY` is configured.
