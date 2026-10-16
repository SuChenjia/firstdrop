English | [中文](mcp-setup.zh-CN.md)

# MCP Server Setup Guide

FirstDrop ships with an MCP server (stdio JSON-RPC, zero third-party dependencies) that lets AI tools such as Claude Desktop and Cursor query trending data directly.

## Available Tools

| Tool | Description | Parameters |
|------|-------------|------------|
| `get_trending` | Current trending topics (cross-platform clusters) | `limit` (default 20) |
| `get_event_origin` | Earliest known source of an event (origin tracing) | `cluster_id` (required) |
| `search_events` | Search event clusters by keyword | `keyword` (required), `limit` (default 10) |
| `get_daily_brief` | Get the latest daily briefing | none |

## Run Standalone

```bash
cd firstdrop
python -m app.mcp_server
```

## Claude Desktop Configuration

Edit the Claude Desktop config file (`%APPDATA%\Claude\claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "firstdrop": {
      "command": "python",
      "args": ["-m", "app.mcp_server"],
      "cwd": "/path/to/firstdrop"
    }
  }
}
```

> Replace `cwd` with the actual project path (Windows example: `"E:/projects/firstdrop"`).

## Cursor Configuration

Edit Cursor's MCP config file (`~/.cursor/mcp.json` or project-level `.cursor/mcp.json`):

```json
{
  "mcpServers": {
    "firstdrop": {
      "command": "python",
      "args": ["-m", "app.mcp_server"],
      "cwd": "/path/to/firstdrop"
    }
  }
}
```

> Same as above — replace with your actual project path.

## Usage Examples

Once configured, just ask in Claude / Cursor:

- "What's trending across the Chinese internet right now?"
- "Find the earliest source of events related to 'Zheng Qinwen'"
- "Get the latest daily briefing"

## Notes

- The MCP server uses **stdio** transport — it is not an HTTP service
- Run at least one collection cycle first (`python -m app.runner --once`) so there is data to query
- All data is read-only; the MCP server never modifies the database
