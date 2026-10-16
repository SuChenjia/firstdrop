[English](mcp-setup.md) | 中文

# MCP Server 接入指南

FirstDrop 提供 MCP Server（stdio JSON-RPC，零第三方依赖），让 Claude Desktop、Cursor 等 AI 工具直接查询热点数据。

## 可用工具

| 工具名 | 说明 | 参数 |
|--------|------|------|
| `get_trending` | 当前全网热点（跨平台话题簇） | `limit`（默认 20） |
| `get_event_origin` | 某事件的最早出处（首发溯源） | `cluster_id`（必填） |
| `search_events` | 按关键词搜索事件簇 | `keyword`（必填）、`limit`（默认 10） |
| `get_daily_brief` | 获取最新舆情日报 | 无 |

## 启动方式

```bash
cd firstdrop
python -m app.mcp_server
```

## Claude Desktop 配置

编辑 Claude Desktop 配置文件（`%APPDATA%\Claude\claude_desktop_config.json`）：

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

> 将 `cwd` 替换为你的项目实际路径（Windows 示例：`"E:/projects/firstdrop"`）。

## Cursor 配置

编辑 Cursor 的 MCP 配置文件（`~/.cursor/mcp.json` 或项目级 `.cursor/mcp.json`）：

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

> 同上，替换为你的项目实际路径。

## 使用示例

配置完成后，在 Claude / Cursor 中直接提问：

- "现在全网热点是什么？"
- "帮我查一下「郑钦文」相关事件的最早出处"
- "获取最新的舆情日报"

## 注意事项

- MCP Server 使用 **stdio** 传输，不是 HTTP 服务
- 首次使用前请确保已执行过采集（`python -m app.runner --once`）
- 所有数据只读，MCP 不会修改数据库
