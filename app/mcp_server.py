"""MCP Server：让 Claude / Cursor 查询热点聚合数据。

协议：stdio JSON-RPC（Content-Length 前缀 + JSON 体）。
工具：
  - get_trending(limit)          当前全网热点（跨平台簇）
  - get_event_origin(cluster_id) 某事件最早出处（首发溯源）
  - search_events(keyword, limit) 关键词查事件簇
  - get_daily_brief()            取最新舆情日报

启动：
  python -m app.mcp_server
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from . import db
from .brief import latest_brief

SERVER_INFO = {
    "name": "firstdrop-mcp",
    "version": "0.1.0",
    "description": "FirstDrop 热点聚合与首发溯源数据查询",
}

TOOLS = [
    {
        "name": "get_trending",
        "description": "获取当前全网热点（跨平台话题簇），按峰值热度排序",
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "返回条数", "default": 20},
            },
        },
    },
    {
        "name": "get_event_origin",
        "description": "查询某个事件的最早出处（首发溯源）",
        "inputSchema": {
            "type": "object",
            "properties": {
                "cluster_id": {"type": "integer", "description": "事件簇 ID"},
            },
            "required": ["cluster_id"],
        },
    },
    {
        "name": "search_events",
        "description": "按关键词搜索事件簇（模糊匹配标题）",
        "inputSchema": {
            "type": "object",
            "properties": {
                "keyword": {"type": "string", "description": "搜索关键词"},
                "limit": {"type": "integer", "description": "返回条数", "default": 10},
            },
            "required": ["keyword"],
        },
    },
    {
        "name": "get_daily_brief",
        "description": "获取最新一份舆情日报（HTML/Markdown 文件路径与摘要信息）",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


def _connect():
    conn = db.connect()
    db.init_db(conn)
    return conn


# ------------------------------- 工具实现 -------------------------------
def tool_get_trending(limit: int = 20) -> str:
    conn = _connect()
    try:
        rows = [dict(r) for r in conn.execute(
            "SELECT cluster_id, platform_count, platforms, peak_hot, best_position, label "
            "FROM v_cluster WHERE platform_count>=2 "
            "ORDER BY peak_hot DESC LIMIT ?", (limit,)
        )]
        return json.dumps({"events": rows}, ensure_ascii=False, indent=2)
    finally:
        conn.close()


def tool_get_event_origin(cluster_id: int) -> str:
    conn = _connect()
    try:
        r = conn.execute(
            "SELECT t.cluster_id, t.title, t.tier, t.status, t.platform_count, t.platforms, "
            "s.url AS earliest_url, s.site AS earliest_site, s.posted_at AS earliest_posted_at, "
            "s.title AS earliest_title, s.content_excerpt, s.confidence, s.snapshot_path "
            "FROM event_track t "
            "LEFT JOIN event_earliest_signal s ON s.cluster_id=t.cluster_id "
            "WHERE t.cluster_id=?", (cluster_id,)
        ).fetchone()
        if not r:
            return json.dumps({"error": f"cluster_id={cluster_id} 不存在"}, ensure_ascii=False)
        d = dict(r)
        # 补充各平台最新榜单成员
        d["members"] = [dict(x) for x in conn.execute(
            "SELECT platform_code, title, hot_value, position, url "
            "FROM v_latest_item "
            "WHERE norm_title IN (SELECT norm_title FROM topic_cluster WHERE cluster_id=?) "
            "ORDER BY hot_value DESC", (cluster_id,)
        )]
        return json.dumps(d, ensure_ascii=False, indent=2)
    finally:
        conn.close()


def tool_search_events(keyword: str, limit: int = 10) -> str:
    conn = _connect()
    try:
        rows = [dict(r) for r in conn.execute(
            "SELECT cluster_id, platform_count, platforms, peak_hot, best_position, label "
            "FROM v_cluster WHERE label LIKE ? "
            "ORDER BY peak_hot DESC LIMIT ?", (f"%{keyword}%", limit)
        )]
        return json.dumps({"events": rows, "keyword": keyword}, ensure_ascii=False, indent=2)
    finally:
        conn.close()


def tool_get_daily_brief() -> str:
    b = latest_brief()
    if not b["exists"]:
        return json.dumps({"error": "尚未生成任何日报"}, ensure_ascii=False)
    # 返回文件路径 + 摘要（不读全文，避免 token 爆炸）
    out = {
        "date": b["date"],
        "html_path": b["html_path"],
        "md_path": b.get("md_path"),
        "html_size": b["html_size"],
        "note": "请用 Read 工具打开 html_path 或 md_path 查看完整内容",
    }
    return json.dumps(out, ensure_ascii=False, indent=2)


TOOL_HANDLERS = {
    "get_trending": lambda args: tool_get_trending(args.get("limit", 20)),
    "get_event_origin": lambda args: tool_get_event_origin(args["cluster_id"]),
    "search_events": lambda args: tool_search_events(args["keyword"], args.get("limit", 10)),
    "get_daily_brief": lambda args: tool_get_daily_brief(),
}


# ------------------------------- JSON-RPC -------------------------------
def _send(obj: dict) -> None:
    body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    sys.stdout.buffer.write(f"Content-Length: {len(body)}\r\n\r\n".encode("utf-8") + body)
    sys.stdout.buffer.flush()


def _recv() -> dict | None:
    """读取一条 Content-Length 前缀的 JSON-RPC 消息。"""
    headers = {}
    while True:
        line = sys.stdin.buffer.readline()
        if not line:
            return None
        line = line.strip()
        if not line:
            break
        if b":" in line:
            k, v = line.split(b":", 1)
            headers[k.strip().lower()] = v.strip()
    length = int(headers.get(b"content-length", 0))
    if length <= 0:
        return None
    raw = sys.stdin.buffer.read(length)
    try:
        return json.loads(raw.decode("utf-8"))
    except Exception:
        return None


def _result(req_id, result):
    _send({"jsonrpc": "2.0", "id": req_id, "result": result})


def _error(req_id, code: int, message: str):
    _send({"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}})


def _handle_request(req: dict) -> None:
    rid = req.get("id")
    method = req.get("method", "")

    if method == "initialize":
        _result(rid, {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": SERVER_INFO,
        })
        return

    if method == "notifications/initialized":
        return  # 无响应

    if method == "tools/list":
        _result(rid, {"tools": TOOLS})
        return

    if method == "tools/call":
        name = (req.get("params") or {}).get("name", "")
        args = (req.get("params") or {}).get("arguments") or {}
        handler = TOOL_HANDLERS.get(name)
        if not handler:
            _error(rid, -32601, f"工具不存在: {name}")
            return
        try:
            text = handler(args)
            _result(rid, {"content": [{"type": "text", "text": text}]})
        except Exception as e:
            _error(rid, -32603, f"{type(e).__name__}: {e}")
        return

    if method == "ping":
        _result(rid, {})
        return

    _error(rid, -32601, f"未知方法: {method}")


def main() -> int:
    while True:
        req = _recv()
        if req is None:
            break
        try:
            _handle_request(req)
        except Exception as e:
            _error(req.get("id"), -32603, f"{type(e).__name__}: {e}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
