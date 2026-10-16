"""内置看板服务：读取 SQLite，暴露 JSON API + 单页展示。

启动：
  python -m app.web --port 8000 [--db path]
访问 http://localhost:8000/
"""
from __future__ import annotations

import argparse
import base64
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import config as src_config
from . import db
from . import trace as trace_mod

TEMPLATE = Path(__file__).resolve().parent / "templates" / "index.html"
DEMO_TEMPLATE = Path(__file__).resolve().parent / "templates" / "demo.html"
EVENT_TEMPLATE = Path(__file__).resolve().parent / "templates" / "event.html"

# 管理接口鉴权：设置 ADMIN_TOKEN 后，所有管理类 GET/POST 均强制校验
# Authorization: Bearer <ADMIN_TOKEN>（不再对 loopback 放行——反代/隧道场景下
# 转发后源地址也是 127.0.0.1，loopback 例外会被旁路）。
# 未配置 token 时，默认仅允许直接本机访问（带 CF-Connecting-IP / X-Forwarded-For
# 代理头的请求视为外部流量，一律拒绝）。
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "").strip()

# 公开只读 GET API（Demo/事件详情页依赖，不鉴权）
PUBLIC_GET_PATHS = {
    "/api/summary", "/api/clusters", "/api/event", "/api/trace",
    "/api/briefs", "/api/snapshot",
}


def _sync_sources(conn) -> None:
    """把 config/sources.toml 同步进 platform 表，让看板的名称/启用态与配置一致。"""
    try:
        cfg = src_config.load()
    except FileNotFoundError:
        return
    try:
        src_config.sync_platforms(conn, cfg)
    except Exception:  # noqa: BLE001  配置/迁移异常不应导致看板不可用
        pass


# ---------------------------------------------------------------- API 实现
def api_summary(conn) -> dict:
    health = [dict(r) for r in conn.execute("SELECT * FROM v_run_health ORDER BY platform_code")]
    counts = conn.execute(
        "SELECT (SELECT COUNT(*) FROM collect_run) runs, "
        "       (SELECT COUNT(*) FROM hot_item) items, "
        "       (SELECT COUNT(*) FROM (SELECT 1 FROM hot_item GROUP BY platform_code, norm_title)) topics, "
        "       (SELECT COUNT(*) FROM v_cluster) clusters, "
        "       (SELECT COUNT(*) FROM v_cluster WHERE platform_count>=2) multi_clusters, "
        "       (SELECT COUNT(*) FROM alert) alerts, "
        "       (SELECT COUNT(*) FROM alert WHERE level='high') alerts_high, "
        "       (SELECT COUNT(*) FROM v_unified_word) unified, "
        "       (SELECT COUNT(*) FROM word_metric WHERE is_filtered=1) filtered, "
        "       (SELECT COUNT(*) FROM entity_metric) entities, "
        "       (SELECT COUNT(*) FROM entity_metric WHERE platform_count>=2) multi_entities, "
        "       (SELECT COUNT(*) FROM entity_alias_suggest WHERE status='pending') suggests, "
        "       (SELECT COUNT(*) FROM event_earliest_signal) with_signal"
    ).fetchone()
    platforms = [dict(r) for r in conn.execute(
        "SELECT code, name FROM platform WHERE enabled=1 ORDER BY sort_order")]
    return {"counts": dict(counts), "health": health, "platforms": platforms}


def api_latest(conn, qs: dict) -> dict:
    platform = (qs.get("platform", ["all"])[0] or "all")
    limit = int(qs.get("limit", ["50"])[0])
    names = {r["code"]: r["name"] for r in conn.execute("SELECT code, name FROM platform")}

    sql = ("SELECT platform_code, position, is_top, title, norm_title, external_id, url, "
           "hot_value, hot_text, label, author, source, captured_at FROM v_latest_item")
    params = ()
    if platform != "all":
        sql += " WHERE platform_code=?"
        params = (platform,)
    sql += " ORDER BY platform_code, position"

    boards: dict[str, list] = {}
    for r in conn.execute(sql, params):
        boards.setdefault(r["platform_code"], []).append(dict(r))
    for code in boards:
        boards[code] = boards[code][:limit]
    return {"boards": boards, "names": names}


def api_unified(conn, qs: dict) -> dict:
    """统一标准表：跨平台归一化热度（0~10000）横向可比。"""
    limit = int(qs.get("limit", ["300"])[0])
    category = _decode_param(qs.get("category", [None])[0])
    platform = qs.get("platform", [None])[0]
    trend = _decode_param(qs.get("trend", [None])[0])

    sql = ("SELECT 词, 来源平台, 热度口径, 热度原始值, 归一化热度, 环比增长率, "
           "上榜时间, 分类标签, 趋势标记, 来源 FROM v_unified_word")
    conds, params = [], ()
    if category and category != "all":
        conds.append("分类标签=?")
        params += (category,)
    if platform and platform != "all":
        conds.append("来源平台=?")
        params += (platform,)
    if trend and trend != "all":
        conds.append("趋势标记=?")
        params += (trend,)
    if conds:
        sql += " WHERE " + " AND ".join(conds)
    sql += " ORDER BY 归一化热度 DESC LIMIT ?"
    params += (limit,)

    rows = [dict(r) for r in conn.execute(sql, params)]
    cats = [r["c"] for r in conn.execute(
        "SELECT DISTINCT 分类标签 AS c FROM v_unified_word ORDER BY c")]
    trends = [r["t"] for r in conn.execute(
        "SELECT DISTINCT 趋势标记 AS t FROM v_unified_word ORDER BY t")]
    return {"rows": rows, "categories": cats, "trends": trends}


def api_clusters(conn, qs: dict) -> dict:
    limit = int(qs.get("limit", ["100"])[0])
    min_pc = int(qs.get("min_platforms", ["1"])[0])
    rows = [dict(r) for r in conn.execute(
        "SELECT cluster_id, platform_count, platforms, topic_count, peak_hot, heat_sum, "
        "best_position, label FROM v_cluster WHERE platform_count>=? "
        "ORDER BY platform_count DESC, peak_hot DESC LIMIT ?", (min_pc, limit))]
    return {"clusters": rows}


def api_entities(conn, qs: dict) -> dict:
    """实体归一：一行 = 一个实体（同词融合），双口径聚合热度并列。"""
    limit = int(qs.get("limit", ["200"])[0])
    min_pc = int(qs.get("min_platforms", ["1"])[0])
    sort = qs.get("sort", ["joint"])[0]           # joint（联合式，默认）| peak（峰值式）
    order = "聚合热度_峰值式" if sort == "peak" else "聚合热度_联合式"
    category = _decode_param(qs.get("category", [None])[0])

    sql = ("SELECT 实体ID, 实体, 分类标签, 聚合热度_峰值式, 聚合热度_联合式, 覆盖平台数, "
           "覆盖平台, 单平台峰值, 扩散加成, 最佳名次, 上榜时间, 趋势标记 FROM v_unified_entity")
    conds, params = ["覆盖平台数>=?"], (min_pc,)
    if category and category != "all":
        conds.append("分类标签=?")
        params += (category,)
    sql += " WHERE " + " AND ".join(conds)
    sql += f" ORDER BY {order} DESC LIMIT ?"
    params += (limit,)

    rows = [dict(r) for r in conn.execute(sql, params)]
    cats = [r["c"] for r in conn.execute(
        "SELECT DISTINCT 分类标签 AS c FROM v_unified_entity ORDER BY c")]
    return {"rows": rows, "categories": cats}


def api_alerts(conn, qs: dict) -> dict:
    limit = int(qs.get("limit", ["100"])[0])
    level = qs.get("level", [None])[0]
    sql = ("SELECT id, created_at, scope, platform_code, norm_title, cluster_id, title, "
           "level, score, signals_json FROM alert")
    params: tuple = ()
    if level:
        sql += " WHERE level=?"
        params = (level,)
    sql += " ORDER BY CASE level WHEN 'high' THEN 0 WHEN 'medium' THEN 1 WHEN 'low' THEN 2 ELSE 3 END, created_at DESC LIMIT ?"
    params = params + (limit,)
    alerts = []
    for r in conn.execute(sql, params):
        d = dict(r)
        try:
            d["signals"] = json.loads(d.pop("signals_json") or "{}")
        except Exception:  # noqa: BLE001
            d["signals"] = {}
        alerts.append(d)
    return {"alerts": alerts}


def api_config(conn) -> dict:
    """读数据源配置（config/sources.toml）：开关 / 间隔 / 重试 + 可用适配器清单。"""
    cfg = src_config.load()
    return {
        "path": str(cfg.path),
        "enrich_interval_sec": cfg.enrich_interval_sec,
        "trace_daily_quota": cfg.trace_daily_quota,
        "defaults": dict(src_config.DEFAULTS),
        "available_adapters": src_config.available_adapters(),
        "sources": [{
            "code": s.code, "name": s.name, "adapter": s.adapter, "enabled": s.enabled,
            "interval_sec": s.interval_sec, "max_retries": s.max_retries,
            "retry_delay_sec": s.retry_delay_sec, "sort_order": s.sort_order,
        } for s in cfg.sources],
    }


def api_save_config(conn, body: dict) -> dict:
    """看板提交 → 校验 → 写回 config/sources.toml → 同步 platform 表。"""
    sources = body.get("sources")
    if not isinstance(sources, list):
        return {"error": "参数不合法：需要 sources 列表"}
    try:
        cfg = src_config.build(sources, body.get("enrich_interval_sec"),
                               trace_daily_quota=body.get("trace_daily_quota"))
        src_config.save(cfg)
    except Exception as e:  # noqa: BLE001
        return {"error": f"{type(e).__name__}: {e}"}
    sync = src_config.sync_platforms(conn, cfg)
    return {"ok": True, "sync": sync, "config": api_config(conn)}


def api_suggest(conn, qs: dict) -> dict:
    """别名候选（entity_alias_suggest）：默认看全部，可筛选 pending/approved/rejected。

    返回行内附 impact_topics / impact_platforms（反事实评估：该别名若启用将合并的
    话题数/平台数）。排序：待审最前；待审内部按影响降序——影响 >0 的排前，
    「当前影响 0」的自动落到末尾（即低优先级分区，但**不丢弃**，符合 Q1-B）。
    """
    status = qs.get("status", ["all"])[0]
    limit = int(qs.get("limit", ["500"])[0])
    sql = ("SELECT alias_norm, canonical, source, evidence, status, created_at, "
           "impact_topics, impact_platforms FROM entity_alias_suggest")
    params: tuple = ()
    if status and status != "all":
        sql += " WHERE status=?"
        params += (status,)
    sql += (" ORDER BY CASE status WHEN 'pending' THEN 0 WHEN 'approved' THEN 1 ELSE 2 END, "
            "(COALESCE(impact_topics,0)=0), COALESCE(impact_topics,0) DESC, "
            "created_at DESC, alias_norm LIMIT ?")
    params += (limit,)
    rows = [dict(r) for r in conn.execute(sql, params)]
    counts = {r["status"]: r["c"] for r in conn.execute(
        "SELECT status, COUNT(*) c FROM entity_alias_suggest GROUP BY status")}
    counts["pending_active"] = conn.execute(
        "SELECT COUNT(*) FROM entity_alias_suggest WHERE status='pending' "
        "AND impact_topics > 0").fetchone()[0]
    return {"rows": rows, "counts": counts}


def api_review(conn, body: dict) -> dict:
    """审核别名候选：approve → 写入正式词典 entity_alias 并启用；reject → 标记 rejected；
    revoke → 撤销已通过/已驳回，退回待审，并移除该次自动写入的词典条目（种子词不受影响）。

    审核只改词典数据，不触发重新对齐；下次采集/加工（enrich）时自动生效。
    """
    alias = (body.get("alias_norm") or "").strip()
    canon = (body.get("canonical") or "").strip()
    action = (body.get("action") or "").strip()
    if not alias or not canon or action not in ("approve", "reject", "revoke"):
        return {"error": "参数不合法：需要 alias_norm / canonical / action(approve|reject|revoke)"}

    row = conn.execute(
        "SELECT status FROM entity_alias_suggest WHERE alias_norm=? AND canonical=?",
        (alias, canon)).fetchone()
    if not row:
        return {"error": "候选不存在", "alias_norm": alias, "canonical": canon}

    if action == "approve":
        conn.execute(
            "INSERT INTO entity_alias (alias_norm, canonical, kind, enabled, source) "
            "VALUES (?,?,?,1,'auto') "
            "ON CONFLICT(alias_norm) DO UPDATE SET canonical=excluded.canonical, "
            "enabled=1, source='auto'",
            (alias, canon, "auto"))
        status = "approved"
    elif action == "reject":
        status = "rejected"
    else:  # revoke：退回待审；仅删除本次审核（source='auto'）写入的词典条目
        conn.execute("DELETE FROM entity_alias WHERE alias_norm=? AND source='auto'", (alias,))
        status = "pending"

    conn.execute(
        "UPDATE entity_alias_suggest SET status=? WHERE alias_norm=? AND canonical=?",
        (status, alias, canon))
    conn.commit()
    return {"ok": True, "status": status, "alias_norm": alias, "canonical": canon}


def api_history(conn, qs: dict) -> dict:
    """历史榜单回溯：按关键词 / 日期 / 平台筛选 hot_item 历史留痕。

    q 用 URL 安全 base64 传参（避免中文出现在 query 串里被中间层破坏）；
    date 为 YYYY-MM-DD（匹配 captured_at 前 10 位）；platform 为平台代码或 all。
    """
    keyword = (_decode_param(qs.get("q", [None])[0]) or "").strip()
    date = (qs.get("date", [None])[0] or "").strip()
    platform = qs.get("platform", [None])[0]
    limit = int(qs.get("limit", ["200"])[0])

    sql = ("SELECT platform_code, position, is_top, title, norm_title, hot_value, "
           "hot_text, label, url, source, captured_at FROM hot_item WHERE is_aggregate=0")
    conds, params = [], ()
    if keyword:
        conds.append("(title LIKE ? OR norm_title LIKE ?)")
        params += (f"%{keyword}%", f"%{keyword}%")
    if date:
        conds.append("substr(captured_at,1,10)=?")
        params += (date,)
    if platform and platform != "all":
        conds.append("platform_code=?")
        params += (platform,)
    if conds:
        sql += " AND " + " AND ".join(conds)
    sql += " ORDER BY captured_at DESC, platform_code, position LIMIT ?"
    params += (limit,)

    rows = [dict(r) for r in conn.execute(sql, params)]
    dates = [r["d"] for r in conn.execute(
        "SELECT DISTINCT substr(captured_at,1,10) AS d FROM hot_item ORDER BY d DESC LIMIT 30")]
    return {"rows": rows, "dates": dates, "limit": limit}


def api_trace(conn, qs: dict) -> dict:
    """首发溯源：事件列表（含首发信号）+ 统计。"""
    from datetime import datetime
    status = qs.get("status", ["all"])[0]
    limit = int(qs.get("limit", ["200"])[0])
    sql = ("SELECT cluster_id, title, tier, status, trigger_reason, platform_count, platforms, "
           "peak_hot, best_position, coverage, trace_count, triggered_at, last_traced_at, "
           "earliest_url, earliest_site, earliest_posted_at, earliest_title, content_excerpt, "
           "source_coverage, confidence, is_manual, searched_at, snapshot_path FROM v_event_track")
    params: tuple = ()
    if status and status != "all":
        sql += " WHERE status=?"
        params += (status,)
    sql += (" ORDER BY CASE status WHEN 'pending' THEN 0 WHEN 'active' THEN 1 ELSE 2 END, "
            "CASE tier WHEN 'L1' THEN 0 ELSE 1 END, triggered_at DESC LIMIT ?")
    params += (limit,)

    rows = []
    for r in conn.execute(sql, params):
        d = dict(r)
        for k in ("coverage", "source_coverage"):
            try:
                d[k] = json.loads(d.get(k) or "[]")
            except Exception:  # noqa: BLE001
                d[k] = []
        rows.append(d)

    counts = {r["status"]: r["c"] for r in conn.execute(
        "SELECT status, COUNT(*) c FROM event_track GROUP BY status")}
    counts["with_signal"] = conn.execute(
        "SELECT COUNT(*) FROM event_track t JOIN event_earliest_signal s "
        "ON s.cluster_id=t.cluster_id").fetchone()[0]
    today = datetime.now().astimezone().strftime("%Y-%m-%d")
    counts["today_used"] = conn.execute(
        "SELECT COUNT(*) FROM event_track WHERE last_traced_at IS NOT NULL "
        "AND substr(last_traced_at,1,10)=?", (today,)).fetchone()[0]
    counts["quota"] = trace_mod.daily_quota()
    return {"rows": rows, "counts": counts, "sources": [n for n, _ in trace_mod.SOURCES]}


def api_trace_run(conn, body: dict) -> dict:
    """执行溯源：给 cluster_id 则只溯该事件；否则批量处理 limit 个待溯源事件（受每日预算约束）。"""
    cid = int(body.get("cluster_id") or 0)
    if cid:
        row = conn.execute("SELECT title, triggered_at FROM event_track WHERE cluster_id=?",
                           (cid,)).fetchone()
        if not row:
            return {"error": "事件不存在"}
        res = trace_mod.trace_one(conn, cid, row["title"] or "", row["triggered_at"] or "")
        ts = db.now_iso()
        conn.execute("UPDATE event_track SET status='active', trace_count=trace_count+1, "
                     "last_traced_at=?, updated_at=? WHERE cluster_id=?", (ts, ts, cid))
        conn.commit()
        return {"ok": True, "signal": res,
                "stats": {"traced": 1, "signals": 1 if res else 0}}
    limit = int(body.get("limit") or 3)
    reg = trace_mod.register_triggers(conn)
    st = trace_mod.run(conn, limit=limit)
    return {"ok": True, "stats": st, "registered": reg}


def api_trace_status(conn, body: dict) -> dict:
    cid = int(body.get("cluster_id") or 0)
    status = (body.get("status") or "").strip()
    if not trace_mod.set_status(conn, cid, status):
        return {"error": "参数不合法或事件不存在"}
    return {"ok": True, "cluster_id": cid, "status": status}


def api_trace_manual(conn, body: dict) -> dict:
    """人工补充首发链接：直接写为首发信号（is_manual=1），优先级高于自动结果。"""
    cid = int(body.get("cluster_id") or 0)
    url = (body.get("url") or "").strip()
    if not url.startswith(("http://", "https://")):
        return {"error": "需要合法的 http(s) 链接"}
    if not trace_mod.set_manual_signal(conn, cid, url):
        return {"error": "事件不存在"}
    return {"ok": True, "cluster_id": cid}


def api_snapshot(conn, qs: dict) -> dict:
    """提供首发信号快照文件下载。"""
    path = qs.get("path", [""])[0]
    if not path or not path.startswith("snapshots/"):
        return {"error": "非法路径"}
    # 防路径穿越
    path = path.replace("..", "").replace("\\", "/")
    full = Path(__file__).resolve().parent.parent / "data" / path
    if not full.is_file():
        return {"error": "快照不存在"}
    return {"file": str(full), "content_type": "text/html; charset=utf-8"}


def _b64url_decode(s: str) -> str:
    """解码 URL 安全的 base64（避免中文出现在 query 串里被中间层破坏）。"""
    s = s.replace("-", "+").replace("_", "/")
    s += "=" * (-len(s) % 4)
    return base64.b64decode(s).decode("utf-8")


def _decode_param(v: str | None) -> str | None:
    """中文枚举参数（category/trend）统一用 URL 安全 base64 传输。

    兼容直接传明文（ASCII 的 all 或调试用明文）：解码失败即按原文返回。
    """
    if not v or v == "all":
        return v
    try:
        return _b64url_decode(v)
    except Exception:  # noqa: BLE001
        return v


def api_timeline(conn, qs: dict) -> dict:
    platform = qs.get("platform", [None])[0]
    # 前端用 t=<base64url(norm_title)>；同时兼容直接传 norm_title（便于 curl 调试）
    norm_title = None
    if qs.get("t"):
        norm_title = _b64url_decode(qs["t"][0])
    elif qs.get("norm_title"):
        norm_title = qs["norm_title"][0]
    if not platform or not norm_title:
        return {"points": [], "error": "需要 platform 与 t（或 norm_title）"}
    rows = [dict(r) for r in conn.execute(
        "SELECT captured_at, position, hot_value, label FROM v_rank_timeline "
        "WHERE platform_code=? AND norm_title=? ORDER BY captured_at", (platform, norm_title))]
    return {"platform": platform, "norm_title": norm_title, "points": rows}


def api_event(conn, qs: dict) -> dict:
    """事件详情 API：返回簇信息 + 首发溯源 + 各平台成员 + 时间线。"""
    try:
        cid = int(qs.get("id", [0])[0] or 0)
    except (TypeError, ValueError):
        return {"error": "id 参数不合法（需为数字）"}
    if not cid:
        return {"error": "缺少 id 参数"}
    c = conn.execute(
        "SELECT cluster_id, platform_count, platforms, topic_count, "
        "peak_hot, best_position, label FROM v_cluster WHERE cluster_id=?", (cid,)
    ).fetchone()
    if not c:
        return {"error": f"事件不存在: cluster_id={cid}"}
    c = dict(c)
    s = conn.execute(
        "SELECT url, site, posted_at, title, content_excerpt, source_coverage, "
        "confidence, snapshot_path FROM event_earliest_signal WHERE cluster_id=?", (cid,)
    ).fetchone()
    if s:
        s = dict(s)
        try:
            s["source_coverage"] = json.loads(s.get("source_coverage") or "[]")
        except Exception:
            s["source_coverage"] = []
    members = [dict(r) for r in conn.execute(
        "SELECT platform_code, title, hot_value, position, url, captured_at "
        "FROM v_latest_item "
        "WHERE norm_title IN (SELECT norm_title FROM topic_cluster WHERE cluster_id=?) "
        "ORDER BY hot_value DESC", (cid,)
    )]
    timeline = [dict(r) for r in conn.execute(
        "SELECT platform_code, captured_at, position, hot_value FROM v_rank_timeline "
        "WHERE norm_title IN (SELECT norm_title FROM topic_cluster WHERE cluster_id=?) "
        "ORDER BY captured_at", (cid,)
    )]
    return {"cluster": c, "earliest": s, "members": members, "timeline": timeline}


def api_briefs(conn, qs: dict) -> dict:
    """简报历史列表。"""
    from . import brief as brief_mod
    return {"briefs": brief_mod.list_briefs()}


ROUTES = {
    "/api/summary": lambda c, q: api_summary(c),
    "/api/latest": api_latest,
    "/api/unified": api_unified,
    "/api/clusters": api_clusters,
    "/api/entities": api_entities,
    "/api/alerts": api_alerts,
    "/api/timeline": api_timeline,
    "/api/history": api_history,
    "/api/trace": api_trace,
    "/api/suggest": api_suggest,
    "/api/config": lambda c, q: api_config(c),
    "/api/snapshot": api_snapshot,
    "/api/briefs": api_briefs,
    "/api/event": api_event,
}

POST_ROUTES = {
    "/api/suggest/review": api_review,
    "/api/trace/run": api_trace_run,
    "/api/trace/status": api_trace_status,
    "/api/trace/manual": api_trace_manual,
    "/api/config": api_save_config,
}


# ---------------------------------------------------------------- HTTP
class Handler(BaseHTTPRequestHandler):
    db_path = str(db.DEFAULT_DB)

    def _send(self, body: bytes, content_type: str, code: int = 200) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code: int = 200) -> None:
        self._send(json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8", code)

    def do_GET(self):  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)

        if path in ("/", "/index.html"):
            if not TEMPLATE.exists():
                return self._json({"error": "缺少 templates/index.html"}, 500)
            return self._send(TEMPLATE.read_text(encoding="utf-8").encode("utf-8"),
                              "text/html; charset=utf-8")

        if path in ("/demo", "/demo.html"):
            if not DEMO_TEMPLATE.exists():
                return self._json({"error": "缺少 templates/demo.html"}, 500)
            return self._send(DEMO_TEMPLATE.read_text(encoding="utf-8").encode("utf-8"),
                              "text/html; charset=utf-8")

        # 静态文件：简报（data/briefs/）和快照（data/snapshots/）
        if path.startswith("/data/briefs/"):
            # 防路径穿越
            path = path.replace("..", "").replace("\\", "/")
            fp = Path(__file__).resolve().parent.parent / path.lstrip("/")
            if fp.is_file() and fp.suffix in (".html", ".md"):
                ct = "text/html; charset=utf-8" if fp.suffix == ".html" else "text/markdown; charset=utf-8"
                return self._send(fp.read_bytes(), ct)
            return self._json({"error": "not found"}, 404)

        # 事件详情只读页
        if path == "/event":
            if not EVENT_TEMPLATE.exists():
                return self._json({"error": "缺少 templates/event.html"}, 500)
            return self._send(EVENT_TEMPLATE.read_text(encoding="utf-8").encode("utf-8"),
                              "text/html; charset=utf-8")

        handler = ROUTES.get(path)
        if not handler:
            return self._json({"error": "not found", "path": path}, 404)

        # 受保护 GET API：非公开路径统一走管理鉴权
        if path not in PUBLIC_GET_PATHS:
            auth_err = self._check_admin_auth()
            if auth_err:
                code = 401 if "unauthorized" in auth_err else 403
                return self._json({"error": auth_err}, code)

        conn = db.connect(self.db_path)
        _sync_sources(conn)
        try:
            res = handler(conn, qs)
            # 特殊处理：快照文件下载
            if isinstance(res, dict) and "file" in res and "content_type" in res:
                fp = Path(res["file"])
                if fp.is_file():
                    return self._send(fp.read_bytes(), res["content_type"])
                return self._json({"error": "快照不存在"}, 404)
            return self._json(res, 400 if isinstance(res, dict) and res.get("error") else 200)
        except Exception as e:  # noqa: BLE001
            return self._json({"error": f"{type(e).__name__}: {e}"}, 500)
        finally:
            conn.close()

    def _is_local(self) -> bool:
        """判定是否为直接本机访问（非经反代/隧道转发）。"""
        if self.client_address[0] not in ("127.0.0.1", "::1"):
            return False
        # cloudflared/nginx/caddy 等同机代理转发后源地址仍是 loopback，
        # 但会携带代理头；存在代理头时不再信任 loopback。
        return not (self.headers.get("CF-Connecting-IP")
                    or self.headers.get("X-Forwarded-For"))

    def _check_admin_auth(self) -> str | None:
        """管理接口鉴权。返回 None 表示通过，否则返回错误信息字符串。"""
        if ADMIN_TOKEN:
            auth = self.headers.get("Authorization", "")
            if auth != f"Bearer {ADMIN_TOKEN}":
                return "unauthorized：需要 Authorization: Bearer <ADMIN_TOKEN>"
            return None
        if not self._is_local():
            return "禁止外部访问：服务端未配置 ADMIN_TOKEN"
        return None

    def do_POST(self):  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            body = json.loads(raw.decode("utf-8") or "{}")
        except Exception:  # noqa: BLE001
            body = {}

        # 管理接口鉴权（配了 token 时一律强制校验，不再区分 loopback）
        auth_err = self._check_admin_auth()
        if auth_err:
            code = 401 if "unauthorized" in auth_err else 403
            return self._json({"error": auth_err}, code)

        if path not in POST_ROUTES:
            return self._json({"error": "not found", "path": path}, 404)

        conn = db.connect(self.db_path)
        _sync_sources(conn)
        try:
            res = POST_ROUTES[path](conn, body)
            return self._json(res, 400 if res.get("error") else 200)
        except Exception as e:  # noqa: BLE001
            return self._json({"error": f"{type(e).__name__}: {e}"}, 500)
        finally:
            conn.close()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="热点聚合看板服务")
    ap.add_argument("--db", default=str(db.DEFAULT_DB))
    ap.add_argument("--host", default=None,
                    help="监听地址；缺省：配置 ADMIN_TOKEN 时 0.0.0.0，否则 127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    args = ap.parse_args(argv)

    if args.host is None:
        args.host = "0.0.0.0" if ADMIN_TOKEN else "127.0.0.1"

    Handler.db_path = args.db
    conn = db.connect(args.db)
    db.init_db(conn)
    conn.close()

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    tip = ("已配置 ADMIN_TOKEN：非本机写接口需携带 Authorization: Bearer <token>"
           if ADMIN_TOKEN else "未配置 ADMIN_TOKEN：非本机来源禁止调用管理接口")
    print(f"看板已启动: http://localhost:{args.port}/  (数据库: {args.db}, 监听 {args.host})")
    print(f"  鉴权状态: {tip}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
