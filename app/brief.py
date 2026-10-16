"""舆情日报生成器：从事件簇 + 首发溯源产出每日简报。

调用链：
  1. 从 v_cluster / v_event_track 取今日活跃事件 Top N；
  2. 按话题对齐成员补充各平台榜单表现；
  3.（可选）LLM 润色事件摘要与传播推断；
  4. 输出 HTML / Markdown 到 data/briefs/YYYYMMDD.{html,md}。

LLM 接入（零依赖，纯 urllib）：
  · 默认从环境变量读取 LLM_BRIEF_PROVIDER + LLM_BRIEF_API_KEY + LLM_BRIEF_MODEL；
  · 支持 openai / deepseek / openrouter 三种 provider（openrouter 用于聚合任意兼容 API）；
  · 缺省 provider 为空 → 使用规则模板兜底（不产生 LLM 调用）。

规则模板兜底：
  · 标题、摘要、各平台表现、首发出处均直接取自数据库；
  · 不虚构任何不存在的信息。
"""
from __future__ import annotations

import json
import os
import re
import urllib.request
from datetime import datetime, timedelta
from html import escape as esc
from pathlib import Path

from . import db

BRIEF_DIR = Path(__file__).resolve().parent.parent / "data" / "briefs"
BRIEF_DIR.mkdir(parents=True, exist_ok=True)

# ---- LLM 调参（全部走环境变量，缺省为空=不使用 LLM）--------------------
PROVIDER = os.getenv("LLM_BRIEF_PROVIDER", "").strip().lower()      # openai / deepseek / openrouter
API_KEY = os.getenv("LLM_BRIEF_API_KEY", "").strip()
MODEL = os.getenv("LLM_BRIEF_MODEL", "")
API_BASE = os.getenv("LLM_BRIEF_API_BASE", "").strip()              # 自定义 endpoint

# 各 provider 默认 endpoint
_DEFAULT_BASE = {
    "openai": "https://api.openai.com/v1",
    "deepseek": "https://api.deepseek.com",
    "openrouter": "https://openrouter.ai/api/v1",
}

TOP_EVENTS = 10           # 日报收录事件数上限
LLM_TIMEOUT = 60          # LLM 单次超时（秒）

UA = "FirstDrop-Brief/1.0"


# ============================== 数据层 ==============================
def _now():
    return datetime.now().astimezone()


def _today_range() -> tuple[str, str]:
    """返回今日起止 ISO 时间（本地时区）。"""
    today = _now().replace(hour=0, minute=0, second=0, microsecond=0)
    tomorrow = today + timedelta(days=1)
    return today.isoformat(timespec="seconds"), tomorrow.isoformat(timespec="seconds")


def fetch_events(conn, limit: int = TOP_EVENTS, date: str | None = None) -> list[dict]:
    """取指定日期（默认今日）活跃的跨平台簇（platform_count>=2），附带首发信号。"""
    if date:
        try:
            d = datetime.strptime(date, "%Y-%m-%d")
            day0 = d.replace(hour=0, minute=0, second=0, microsecond=0,
                             tzinfo=_now().tzinfo)
        except ValueError:
            day0 = _now().replace(hour=0, minute=0, second=0, microsecond=0)
    else:
        day0 = _now().replace(hour=0, minute=0, second=0, microsecond=0)
    day1 = day0 + timedelta(days=1)
    # 以 topic_cluster.updated_at 作为"活跃"的代理标记
    sql = """
    SELECT
      c.cluster_id, c.platform_count, c.platforms,
      m.peak_hot, m.best_position, m.label, m.topic_count,
      t.tier, t.status,
      s.url AS earliest_url, s.site AS earliest_site,
      s.posted_at AS earliest_posted_at, s.title AS earliest_title,
      s.content_excerpt, s.confidence
    FROM (
      SELECT cluster_id, platform_count, platforms
      FROM v_cluster WHERE platform_count>=2
    ) c
    LEFT JOIN (
      SELECT cluster_id, peak_hot, best_position, label, topic_count
      FROM v_cluster
    ) m ON m.cluster_id = c.cluster_id
    LEFT JOIN event_track t ON t.cluster_id = c.cluster_id
    LEFT JOIN event_earliest_signal s ON s.cluster_id = c.cluster_id
    WHERE EXISTS (
      SELECT 1 FROM topic_cluster tc
      WHERE tc.cluster_id = c.cluster_id
        AND tc.updated_at >= ? AND tc.updated_at < ?
    )
    ORDER BY m.peak_hot DESC, c.platform_count DESC, c.cluster_id DESC
    LIMIT ?
    """
    rows = [dict(r) for r in conn.execute(sql, (day0.isoformat(timespec="seconds"),
                                                  day1.isoformat(timespec="seconds"), limit))]
    # 补各平台最新榜单成员
    for r in rows:
        r["members"] = [dict(x) for x in conn.execute(
            "SELECT platform_code, title, hot_value, position, label, url "
            "FROM v_latest_item "
            "WHERE norm_title IN (SELECT norm_title FROM topic_cluster WHERE cluster_id=?) "
            "ORDER BY hot_value DESC",
            (r["cluster_id"],)
        )]
    return rows


def fetch_alerts(conn, limit: int = 6) -> list[dict]:
    """今日高优先级预警。"""
    today0, today1 = _today_range()
    sql = ("SELECT id, created_at, scope, platform_code, norm_title, title, level, score "
           "FROM alert WHERE level='high' AND created_at>=? AND created_at<? "
           "ORDER BY created_at DESC LIMIT ?")
    return [dict(r) for r in conn.execute(sql, (today0, today1, limit))]


def fetch_summary_stats(conn) -> dict:
    """今日总览数字。"""
    today = _now().strftime("%Y-%m-%d")
    stats = conn.execute(
        "SELECT "
        "  (SELECT COUNT(DISTINCT cluster_id) FROM topic_cluster "
        "   WHERE substr(updated_at,1,10)=?) active_clusters, "
        "  (SELECT COUNT(*) FROM alert WHERE substr(created_at,1,10)=?) alerts_today, "
        "  (SELECT COUNT(*) FROM alert WHERE level='high' AND substr(created_at,1,10)=?) alerts_high_today, "
        "  (SELECT COUNT(*) FROM event_earliest_signal WHERE substr(searched_at,1,10)=?) traced_today, "
        "  (SELECT COUNT(*) FROM hot_item WHERE substr(captured_at,1,10)=?) items_today, "
        "  (SELECT COUNT(DISTINCT platform_code) FROM collect_run WHERE status='ok' "
        "   AND substr(started_at,1,10)=?) platforms_ok_today",
        (today, today, today, today, today, today)
    ).fetchone()
    return dict(stats)


# ============================== LLM 层 ==============================
def _build_prompt(events: list[dict], alerts: list[dict], stats: dict) -> str:
    """构造 LLM 系统 + user prompt，要求输出结构化 JSON。"""
    sys_msg = (
        "你是 FirstDrop 舆情分析助手，负责撰写客观、克制的中文舆情日报。"
        "你只能基于提供的数据撰写，不得编造任何不存在的信息。"
        "输出必须是严格 JSON：{\"overview\":\"...\", \"events\":[{\"title\":\"...\", \"summary\":\"...\", \"spread\":\"...\"},...]}"
    )
    lines = [
        f"今日活跃事件数：{stats.get('active_clusters',0)}",
        f"今日预警数：{stats.get('alerts_today',0)}（高优先级 {stats.get('alerts_high_today',0)}）",
        f"今日新溯源：{stats.get('traced_today',0)}",
        f"今日采集条目：{stats.get('items_today',0)}",
        f"参与平台：{stats.get('platforms_ok_today',0)}",
        "\n事件列表（按峰值热度排序）：",
    ]
    for e in events:
        members = e.get("members", [])
        plat_line = ", ".join(
            [f"{m['platform_code']}#{m['position']}（{m['hot_value'] or 0}）" for m in members[:5]]
        )
        earliest = f" 首发：{e.get('earliest_site','未知')} {e.get('earliest_title','')}" if e.get("earliest_url") else ""
        lines.append(f"- {e.get('label','—')} | 跨{e.get('platform_count',0)}平台 | 峰值{e.get('peak_hot',0)} | {plat_line}{earliest}")
    if alerts:
        lines.append("\n今日高优先级预警：")
        for a in alerts:
            lines.append(f"- {a.get('level','')} | {a.get('title','')} | {a.get('score','')}")
    user_msg = "\n".join(lines)
    return sys_msg, user_msg


def _chat_completion(sys_msg: str, user_msg: str) -> dict | None:
    """调用 LLM 并尝试解析返回 JSON。"""
    if not PROVIDER or not API_KEY:
        return None
    base = API_BASE or _DEFAULT_BASE.get(PROVIDER, _DEFAULT_BASE["openai"])
    model = MODEL or {
        "openai": "gpt-4o-mini",
        "deepseek": "deepseek-chat",
        "openrouter": "openai/gpt-4o-mini",
    }.get(PROVIDER, "gpt-4o-mini")

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": sys_msg},
            {"role": "user", "content": user_msg},
        ],
        "temperature": 0.4,
        "max_tokens": 4096,
    }
    req = urllib.request.Request(
        f"{base}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {API_KEY}",
            "User-Agent": UA,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=LLM_TIMEOUT) as r:
            data = json.loads(r.read().decode("utf-8"))
            content = data["choices"][0]["message"]["content"]
            # 从 markdown code block 中提取 JSON
            m = re.search(r"```(?:json)?\s*([\s\S]*?)```", content)
            if m:
                content = m.group(1)
            return json.loads(content)
    except Exception as e:
        print(f"[brief] LLM 调用失败: {e}")
        return None


# ============================== 输出层 ==============================
# 界面文案双语字典：事件标题/平台名/来源站点名始终保留原文（中文数据即产品本身）。
STR = {
    "zh": {
        "html_lang": "zh-CN",
        "title": "FirstDrop 舆情日报",
        "date_fmt": "%Y年%m月%d日",
        "auto": "自动生成",
        "stats": [("活跃事件", "active_clusters"), ("今日预警", "alerts_today"),
                  ("高优先级", "alerts_high_today"), ("新溯源", "traced_today"),
                  ("采集条目", "items_today")],
        "high_alerts": "今日高优先级预警",
        "events": "热点事件",
        "origin": "首发溯源",
        "platform": "平台表现",
        "unknown": "未知",
        "confidence": "置信度",
        "foot": "FirstDrop · 开源热点聚合分析引擎 · 数据来源于各平台公开榜单",
    },
    "en": {
        "html_lang": "en",
        "title": "FirstDrop Daily Briefing",
        "date_fmt": "%b %d, %Y",
        "auto": "auto-generated",
        "stats": [("Active events", "active_clusters"), ("Alerts today", "alerts_today"),
                  ("High-priority", "alerts_high_today"), ("New traces", "traced_today"),
                  ("Items collected", "items_today")],
        "high_alerts": "High-priority alerts today",
        "events": "Hot events",
        "origin": "First source",
        "platform": "Platform performance",
        "unknown": "Unknown",
        "confidence": "Confidence",
        "foot": "FirstDrop · Open-source trend aggregation engine · Data from public platform rankings",
    },
}


def _auto_summary(e: dict, lang: str = "zh") -> str:
    """无 LLM 时的规则摘要（不虚构信息，仅复述数据口径）。"""
    members = e.get("members", [])
    top_plat = members[0]["platform_code"] if members else ("?" if lang == "en" else "某平台")
    if lang == "en":
        return (f"This topic appeared on {e.get('platform_count', 0)} platforms, "
                f"with peak heat {e.get('peak_hot', 0)} ({top_plat}), "
                f"across {e.get('topic_count', 0)} ranking entries.")
    return f"该话题在 {e.get('platform_count',0)} 个平台出现，最高峰值 {e.get('peak_hot',0)}（{top_plat}），涉及 {e.get('topic_count',0)} 个榜单条目。"


def _auto_spread(e: dict, lang: str = "zh") -> str:
    """无 LLM 时的传播推断。"""
    if not e.get("earliest_url"):
        return "First-source tracing not completed yet." if lang == "en" else "首发溯源尚未完成。"
    site = e.get("earliest_site") or ("Unknown source" if lang == "en" else "未知来源")
    if lang == "en":
        return f"The earliest public record found is from {site}; other platforms picked it up in later collection cycles."
    return f"最早公开记录来自 {site}，其余平台在后续采集周期中陆续上榜。"


def _build_llm_map(events: list[dict], llm: dict | None) -> dict:
    """LLM 结果按 cluster_id 回链，避免数组下标错位。"""
    llm_map: dict = {}
    for le in (llm.get("events", []) if llm else []):
        cid = le.get("cluster_id")
        if cid is not None:
            llm_map[cid] = le
        else:
            lt = le.get("title", "").strip()
            for ev in events:
                if lt and (lt in (ev.get("label") or "") or (ev.get("label") or "") in lt):
                    llm_map[ev.get("cluster_id")] = le
                    break
    return llm_map


def _render_html(events: list[dict], alerts: list[dict], stats: dict,
                 llm: dict | None, lang: str = "zh") -> str:
    t = STR[lang]
    today_str = _now().strftime(t["date_fmt"])
    overview = llm.get("overview", "") if llm else ""
    llm_map = _build_llm_map(events, llm)

    def event_block(e: dict, idx: int):
        # 按 cluster_id 回链 LLM 结果；英文版不套用中文 LLM 文案，改走英文规则模板
        le = llm_map.get(e.get("cluster_id"), {}) if lang == "zh" else {}
        title = le.get("title") or e.get("label") or "—"
        summary = le.get("summary") or _auto_summary(e, lang)
        spread = le.get("spread") or _auto_spread(e, lang)
        members = e.get("members", [])
        plat_bars = "".join([
            f'<span style="font-size:11px;background:#1a2339;padding:3px 8px;border-radius:12px;margin-right:6px;display:inline-block;margin-bottom:4px">'
            f'{esc(m.get("platform_code",""))} #{m.get("position","")} <span style="color:#5b8cff">{m.get("hot_value",0) or 0}</span></span>'
            for m in members[:6]
        ])
        earliest = ""
        if e.get("earliest_url"):
            dt = e.get("earliest_posted_at", "")
            if dt:
                try:
                    dt = datetime.fromisoformat(dt).strftime("%m-%d %H:%M")
                except Exception:
                    pass
            earliest = (
                f'<div style="margin-top:10px;font-size:13px;color:#8a97b5"><b>{esc(t["origin"])}</b>：'
                f'<a href="{esc(e["earliest_url"])}" target="_blank" style="color:#5b8cff;text-decoration:none">'
                f'{esc(e.get("earliest_site") or t["unknown"])}</a> · {esc(dt)} · '
                f'{esc(t["confidence"])} {int((e.get("confidence") or 0)*100)}%</div>'
                if lang == "zh" else
                f'<div style="margin-top:10px;font-size:13px;color:#8a97b5"><b>{esc(t["origin"])}</b>: '
                f'<a href="{esc(e["earliest_url"])}" target="_blank" style="color:#5b8cff;text-decoration:none">'
                f'{esc(e.get("earliest_site") or t["unknown"])}</a> · {esc(dt)} · '
                f'{esc(t["confidence"])} {int((e.get("confidence") or 0)*100)}%</div>'
            )
        return (
            f'<div style="background:#131b2e;border:1px solid #24304a;border-radius:18px;padding:22px;margin-bottom:16px;position:relative;overflow:hidden">'
            f'<div style="position:absolute;top:0;left:0;right:0;height:3px;background:linear-gradient(90deg,#5b8cff,#7c5cff)"></div>'
            f'<div style="font-size:18px;font-weight:700;margin-bottom:10px">{esc(title)}</div>'
            f'<div style="font-size:14px;color:#c0c8dc;line-height:1.7;margin-bottom:12px">{esc(summary)}</div>'
            f'<div style="font-size:13px;color:#8a97b5;margin-bottom:10px">{esc(spread)}</div>'
            f'<div>{plat_bars}</div>{earliest}</div>'
        )

    alert_html = ""
    if alerts:
        alert_html = '<div style="background:#1a2339;border:1px solid #24304a;border-radius:14px;padding:18px;margin-bottom:20px;">' \
                     f'<div style="font-weight:700;color:#f87171;margin-bottom:8px">{esc(t["high_alerts"])}</div>' \
                     + "".join([
                         f'<div style="font-size:13px;color:#c0c8dc;padding:4px 0;border-bottom:1px solid #24304a">'
                         f'<span style="color:#f87171;font-weight:600">[{esc(a["level"])}]</span> {esc(a["title"])}</div>'
                         for a in alerts
                     ]) + '</div>'

    stats_html = " ".join([
        f'<span style="display:inline-block;background:#1a2339;padding:6px 14px;border-radius:20px;font-size:12px;margin-right:8px;margin-bottom:6px">{esc(k)} <b style="color:#5b8cff">{stats.get(v, 0)}</b></span>'
        for k, v in t["stats"]
    ])

    body = (
        f'<!doctype html><html lang="{t["html_lang"]}"><head><meta charset="utf-8" />'
        f'<title>{esc(t["title"])} · {esc(today_str)}</title>'
        f'<style>body{{background:#0a0e1a;color:#e9eef8;font:15px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;margin:0;max-width:860px;margin:0 auto;padding:24px}}'
        f'a{{color:#5b8cff}} .muted{{color:#8a97b5}}</style></head><body>'
        f'<div style="text-align:center;margin-bottom:32px"><div style="font-size:32px;font-weight:800;margin-bottom:4px">{esc(t["title"])}</div>'
        f'<div style="font-size:14px;color:#8a97b5">{esc(today_str)} · {esc(t["auto"])}</div></div>'
        f'<div style="margin-bottom:24px">{stats_html}</div>'
        f'{alert_html}'
        f'{"<div style=\"margin-bottom:20px;color:#c0c8dc;font-size:14px;line-height:1.7\">" + esc(overview) + "</div>" if overview else ""}'
        f'{"".join(event_block(e,i) for i,e in enumerate(events))}'
        f'<footer style="margin-top:40px;padding-top:20px;border-top:1px solid #24304a;text-align:center;font-size:12px;color:#5a6680">'
        f'{esc(t["foot"])}</footer></body></html>'
    )
    return body


def _render_md(events: list[dict], alerts: list[dict], stats: dict,
               llm: dict | None, lang: str = "zh") -> str:
    t = STR[lang]
    today_str = _now().strftime(t["date_fmt"])
    overview = llm.get("overview", "") if llm else ""
    llm_map = _build_llm_map(events, llm) if lang == "zh" else {}

    if lang == "en":
        lines = [f"# {t['title']} · {today_str}", ""]
        lines += [f"- **Active events**: {stats.get('active_clusters',0)} | "
                  f"**Alerts today**: {stats.get('alerts_today',0)} "
                  f"(high-priority {stats.get('alerts_high_today',0)}) | "
                  f"**New traces**: {stats.get('traced_today',0)}"]
        lines.append("")
        if overview:
            lines.append(f"> {overview}")
            lines.append("")
        if alerts:
            lines.append(f"## {t['high_alerts']}")
            for a in alerts:
                lines.append(f"- **[{a.get('level','')}]** {a.get('title','')}")
            lines.append("")
        lines.append(f"## {t['events']}")
        for i, e in enumerate(events):
            title = e.get("label") or "—"
            lines.append(f"### {i+1}. {title}")
            lines.append(_auto_summary(e, "en"))
            spread = _auto_spread(e, "en")
            if spread:
                lines.append(f"*{spread}*")
            members = e.get("members", [])
            if members:
                plat = ", ".join([f"{m['platform_code']}#{m['position']} ({m['hot_value'] or 0})" for m in members[:5]])
                lines.append(f"**{t['platform']}**: {plat}")
            if e.get("earliest_url"):
                dt = e.get("earliest_posted_at", "")
                lines.append(f"**{t['origin']}**: [{e.get('earliest_site') or t['unknown']}]({e['earliest_url']}) · {dt} · {t['confidence']} {int((e.get('confidence') or 0)*100)}%")
            lines.append("")
        lines.append("---")
        lines.append(t["foot"])
        return "\n".join(lines)

    lines = [f"# {t['title']} · {today_str}", ""]
    lines += [f"- **活跃事件**：{stats.get('active_clusters',0)} | **今日预警**：{stats.get('alerts_today',0)}（高优先级 {stats.get('alerts_high_today',0)}） | **新溯源**：{stats.get('traced_today',0)}"]
    lines.append("")
    if overview:
        lines.append(f"> {overview}")
        lines.append("")
    if alerts:
        lines.append("## 高优先级预警")
        for a in alerts:
            lines.append(f"- **[{a.get('level','')}]** {a.get('title','')}")
        lines.append("")
    lines.append("## 热点事件")
    for i, e in enumerate(events):
        le = llm_map.get(e.get("cluster_id"), {})
        title = le.get("title") or e.get("label") or "—"
        summary = le.get("summary") or _auto_summary(e)
        spread = le.get("spread") or _auto_spread(e)
        lines.append(f"### {i+1}. {title}")
        lines.append(f"{summary}")
        if spread:
            lines.append(f"*{spread}*")
        members = e.get("members", [])
        if members:
            plat = ", ".join([f"{m['platform_code']}#{m['position']}（{m['hot_value'] or 0}）" for m in members[:5]])
            lines.append(f"**平台表现**：{plat}")
        if e.get("earliest_url"):
            dt = e.get("earliest_posted_at", "")
            lines.append(f"**首发溯源**：[{e.get('earliest_site','未知')}]({e['earliest_url']}) · {dt} · 置信度 {int((e.get('confidence') or 0)*100)}%")
        lines.append("")
    lines.append("---")
    lines.append(t["foot"])
    return "\n".join(lines)


# ============================== CLI ================================
def generate(date: str | None = None, langs: tuple[str, ...] = ("zh", "en")) -> dict:
    """主入口：生成指定日期（默认今日）简报。

    默认同时产出中文（YYYYMMDD.html/.md）与英文（YYYYMMDD-en.html/.md）两份；
    LLM 润色仅作用于中文版，英文版走英文规则模板（不虚构信息）。
    返回 {files: [{lang, html_path, md_path}], event_count, llm_used}。
    """
    conn = db.connect()
    db.init_db(conn)
    try:
        events = fetch_events(conn, date=date)
        alerts = fetch_alerts(conn)
        stats = fetch_summary_stats(conn)
        sys_msg, user_msg = _build_prompt(events, alerts, stats)
        llm = _chat_completion(sys_msg, user_msg) if PROVIDER else None
        date_str = date.replace("-", "") if date else _now().strftime("%Y%m%d")
        files = []
        for lang in langs:
            suffix = "" if lang == "zh" else f"-{lang}"
            # LLM 结果仅注入中文版
            html = _render_html(events, alerts, stats, llm if lang == "zh" else None, lang)
            md = _render_md(events, alerts, stats, llm if lang == "zh" else None, lang)
            hp = BRIEF_DIR / f"{date_str}{suffix}.html"
            mp = BRIEF_DIR / f"{date_str}{suffix}.md"
            hp.write_text(html, encoding="utf-8")
            mp.write_text(md, encoding="utf-8")
            files.append({"lang": lang, "html_path": str(hp), "md_path": str(mp)})
        return {
            "files": files,
            "html_path": files[0]["html_path"],
            "md_path": files[0]["md_path"],
            "event_count": len(events),
            "llm_used": llm is not None,
            "date": date or _now().strftime("%Y-%m-%d"),
        }
    finally:
        conn.close()


def list_briefs() -> list[dict]:
    """列出已生成的简报文件。"""
    out = []
    for p in sorted(BRIEF_DIR.glob("*.html"), reverse=True):
        out.append({
            "date": p.stem,
            "name": p.name,
            "size": p.stat().st_size,
        })
    return out


def latest_brief() -> dict:
    """返回最近一份简报的路径与内容。"""
    files = sorted(BRIEF_DIR.glob("*.html"), reverse=True)
    if not files:
        return {"exists": False}
    latest = files[0]
    md_file = latest.with_suffix(".md")
    return {
        "exists": True,
        "date": latest.stem,
        "html_path": str(latest),
        "md_path": str(md_file) if md_file.exists() else None,
        "html_size": latest.stat().st_size,
    }


if __name__ == "__main__":
    import sys
    res = generate()
    print(json.dumps(res, ensure_ascii=False, indent=2))
