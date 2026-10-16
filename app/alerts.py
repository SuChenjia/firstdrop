"""爆点预警：基于历史留痕识别"正在升温"的话题与跨平台扩散事件。

两类预警：
  scope=topic   单平台话题：新上榜 / 热度飙升 / 名次跃升 / 绝对热度高
  scope=cluster 跨平台簇：同一话题在多个平台同时出现（跨平台扩散）

评分后再分档（high / medium / low）；同一目标在冷却窗口内只报一次，避免刷屏。
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta

from .db import now_iso

# ---- 调参区 -------------------------------------------------------------
COOLDOWN_MIN = 120        # 同一话题/簇的预警冷却（分钟）
TOPIC_LOW, TOPIC_MED, TOPIC_HIGH = 25, 50, 80
CLUSTER_LOW, CLUSTER_MED, CLUSTER_HIGH = 45, 65, 85

TOPIC_SQL = """
SELECT
  cur.platform_code, cur.norm_title, cur.title, cur.position, cur.hot_value, cur.captured_at,
  (SELECT h.position  FROM hot_item h
     WHERE h.platform_code=cur.platform_code AND h.norm_title=cur.norm_title
       AND h.captured_at < cur.captured_at
     ORDER BY h.captured_at DESC LIMIT 1) AS prev_position,
  (SELECT h.hot_value FROM hot_item h
     WHERE h.platform_code=cur.platform_code AND h.norm_title=cur.norm_title
       AND h.captured_at < cur.captured_at
     ORDER BY h.captured_at DESC LIMIT 1) AS prev_hot,
  (SELECT COUNT(DISTINCT h.run_id) FROM hot_item h
     WHERE h.platform_code=cur.platform_code AND h.norm_title=cur.norm_title) AS seen_runs
FROM v_latest_item cur
WHERE cur.is_aggregate = 0
"""

CLUSTER_SQL = """
SELECT cluster_id, platform_count, platforms, peak_hot, best_position, label
FROM v_cluster WHERE platform_count >= 2
"""


def _topic_level(score: float) -> str | None:
    if score >= TOPIC_HIGH:
        return "high"
    if score >= TOPIC_MED:
        return "medium"
    if score >= TOPIC_LOW:
        return "low"
    return None


def _cluster_level(score: float) -> str | None:
    if score >= CLUSTER_HIGH:
        return "high"
    if score >= CLUSTER_MED:
        return "medium"
    if score >= CLUSTER_LOW:
        return "low"
    return None


def _platforms_with_history(conn) -> set[str]:
    """至少在 2 个成功批次里出现过的平台——只有这类平台才有"基线"可比。"""
    return {r["platform_code"] for r in conn.execute(
        "SELECT platform_code FROM collect_run WHERE status='ok' "
        "GROUP BY platform_code HAVING COUNT(*) >= 2")}


def _scan_topics(conn, platforms_with_history: set[str]) -> list[dict]:
    alerts = []
    for r in conn.execute(TOPIC_SQL):
        if r["platform_code"] not in platforms_with_history:
            continue  # 冷启动：没有历史基线，跳过
        pos, hot = r["position"], r["hot_value"]
        prev_pos, prev_hot = r["prev_position"], r["prev_hot"]

        heat_ratio = (hot / prev_hot) if (hot and prev_hot) else None
        pos_gain = (prev_pos - pos) if (prev_pos is not None and pos is not None) else None

        # "新上榜" = 仅在本批次出现过（上一批次没有）。比"首次出现时间"更稳，
        # 老话题不会因为数据库刚建库就被误判为新。
        is_new = r["seen_runs"] <= 1

        score = 0.0
        signals: dict = {}
        if is_new:
            signals["is_new"] = True
            score += 20
        if heat_ratio is not None:
            signals["heat_ratio"] = round(heat_ratio, 2)
            if heat_ratio >= 2.0:
                score += 45
            elif heat_ratio >= 1.5:
                score += 30
            elif heat_ratio >= 1.2:
                score += 15
        if pos_gain is not None and pos_gain > 0:
            signals["pos_gain"] = pos_gain
            if pos_gain >= 10:
                score += 30
            elif pos_gain >= 5:
                score += 20
            elif pos_gain >= 2:
                score += 10
        if hot and hot >= 1_000_000:
            score += 15
            signals["high_heat"] = True
        signals["position"] = pos
        signals["hot_value"] = hot
        signals["seen_runs"] = r["seen_runs"]

        level = _topic_level(score)
        if level:
            alerts.append({
                "scope": "topic", "platform_code": r["platform_code"],
                "norm_title": r["norm_title"], "cluster_id": None,
                "title": r["title"], "level": level, "score": score,
                "signals": signals,
            })
    return alerts


def _scan_clusters(conn) -> list[dict]:
    alerts = []
    for r in conn.execute(CLUSTER_SQL):
        pc = r["platform_count"]
        peak = r["peak_hot"] or 0
        if peak >= 1_000_000:
            bonus = 30
        elif peak >= 100_000:
            bonus = 15
        elif peak >= 10_000:
            bonus = 5
        else:
            bonus = 0
        score = pc * 20 + bonus
        level = _cluster_level(score)
        if not level:
            continue
        alerts.append({
            "scope": "cluster", "platform_code": None, "norm_title": None,
            "cluster_id": r["cluster_id"], "title": r["label"] or "(未命名话题)",
            "level": level, "score": score,
            "signals": {"platform_count": pc, "platforms": r["platforms"],
                        "peak_hot": peak, "best_position": r["best_position"]},
        })
    return alerts


def detect(conn, cooldown_min: int = COOLDOWN_MIN) -> dict:
    """执行一次预警检测并入库（带冷却去重），返回统计信息。"""
    now = datetime.now().astimezone()
    cutoff = (now - timedelta(minutes=cooldown_min)).isoformat(timespec="seconds")

    recent: set[tuple] = set()
    for row in conn.execute(
        "SELECT scope, platform_code, norm_title, cluster_id FROM alert WHERE created_at >= ?",
        (cutoff,),
    ):
        key = (row["platform_code"], row["norm_title"]) if row["scope"] == "topic" \
            else (row["cluster_id"],)
        recent.add((row["scope"],) + key)

    # 先扫描 cluster，得到本轮应报的 cluster_id 集合
    cluster_candidates = _scan_clusters(conn)
    reported_cluster_ids = {c["cluster_id"] for c in cluster_candidates}

    # topic 扫描时，跳过属于已报 cluster 的话题（避免同一事件 topic/cluster 双重预警）
    topic_candidates = []
    for a in _scan_topics(conn, _platforms_with_history(conn)):
        cid = conn.execute(
            "SELECT cluster_id FROM topic_cluster WHERE platform_code=? AND norm_title=?",
            (a["platform_code"], a["norm_title"]),
        ).fetchone()
        if cid and cid[0] in reported_cluster_ids:
            continue  # 该话题所属簇已报 cluster 预警，跳过 topic 级
        topic_candidates.append(a)

    candidates = topic_candidates + cluster_candidates
    ts = now_iso()
    fresh = []
    n_topic = n_cluster = 0
    for a in candidates:
        key = (a["scope"], a["platform_code"], a["norm_title"]) if a["scope"] == "topic" \
            else (a["scope"], a["cluster_id"])
        if key in recent:
            continue
        recent.add(key)
        n_topic += a["scope"] == "topic"
        n_cluster += a["scope"] == "cluster"
        fresh.append((
            ts, a["scope"], a["platform_code"], a["norm_title"], a["cluster_id"],
            a["title"], a["level"], a["score"], json.dumps(a["signals"], ensure_ascii=False),
        ))

    if fresh:
        conn.executemany(
            "INSERT INTO alert (created_at, scope, platform_code, norm_title, cluster_id, "
            "title, level, score, signals_json) VALUES (?,?,?,?,?,?,?,?,?)",
            fresh,
        )
        conn.commit()

    return {"new": len(fresh), "topic": n_topic, "cluster": n_cluster,
            "candidates": len(candidates)}
