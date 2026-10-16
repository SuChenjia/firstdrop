"""数据加工层 pipeline：清洗 → 归一化(0~10000) → 分类 → 环比/趋势 → 统一标准表。

把 raw 的 hot_item（平台各自口径）加工成统一的 word_metric（同一把尺子），
正好产出业务方要求的 8 列：词 / 来源平台 / 热度原始值 / 归一化热度 /
环比增长率 / 上榜时间 / 分类标签 / 趋势标记。

归一化算法：榜内对数分位 + 榜位融合（核心，使跨平台热度可比）
    norm_score = 7000 × 对数分位 + 3000 × 榜位权重        （平台有原始热度）
    norm_score = 10000 × 榜位权重                          （无热度，退化为纯榜位分）
  · 对数分位：对榜内原始热度取 ln 后在 [min, max] 上做归一 → 压平量级差异
  · 榜位权重：(N - 位次 + 1) / N
微博百万级、36氪阅读量级等异构口径由此映射到同一 0~10000 刻度，可横向对比。
每批次的归一化基准（榜内条数/极值/方法）写入 norm_basis，保证可复算、可审计。
"""
from __future__ import annotations

import math

from . import adapters, lexicon
from .db import now_iso

W_QUANTILE = 7000
W_POSITION = 3000
METHOD = "log_quantile+board_pos/7000:3000"

# 环比/趋势阈值
SURGE_GROWTH = 0.30     # 归一化热度环比 ≥ +30% 记为"飙升"
DROP_GROWTH = -0.30     # ≤ -30% 记为"回落"
SURGE_RANK = 5          # 名次前进 ≥ 5 位记为"飙升"
DROP_RANK = -5          # 名次后退 ≥ 5 位记为"回落"

FIRST_SEEN_SQL = """
SELECT platform_code, norm_title, MIN(captured_at) AS first_seen
FROM hot_item GROUP BY platform_code, norm_title
"""

PREV_SQL = """
SELECT norm_score, position FROM word_metric
WHERE platform_code=? AND norm_title=? AND run_id < ?
ORDER BY run_id DESC LIMIT 1
"""

INSERT_SQL = """
INSERT OR REPLACE INTO word_metric
  (run_id, platform_code, norm_title, title, metric_type, hot_value, position,
   norm_score, prev_norm_score, heat_growth, rank_delta, first_seen_at,
   category, category_conf, trend_flag, is_filtered, filter_reason, source, captured_at)
VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
"""


def _clip_position(pos: int | None, n: int) -> int:
    p = pos if isinstance(pos, int) and pos > 0 else 1
    return min(max(p, 1), n)


def _norm_scores(items: list[dict]) -> tuple[dict[str, int], tuple[int, int | None, int | None]]:
    """榜内对数分位 + 榜位融合，返回 ({norm_title: 分数}, (榜单条数, 最小热值, 最大热值))。"""
    n = len(items)
    vals = [it["hot_value"] for it in items if it.get("hot_value") and it["hot_value"] > 0]
    lo = hi = None
    if vals:
        logs = [math.log(v) for v in vals]
        lo, hi = min(logs), max(logs)

    scores: dict[str, int] = {}
    for it in items:
        nt = it["norm_title"]
        if nt in scores:
            continue  # 同批次重复词只取首个
        pos = _clip_position(it.get("position"), n)
        w = (n - pos + 1) / n if n else 0.0
        hv = it.get("hot_value")
        if hv and hv > 0 and hi is not None and hi > lo:
            q = (math.log(hv) - lo) / (hi - lo)
        else:
            q = w  # 无热度或热度全相同：退化为纯榜位分
        scores[nt] = int(round(W_QUANTILE * q + W_POSITION * w))
    v_min = min(vals) if vals else None
    v_max = max(vals) if vals else None
    return scores, (n, v_min, v_max)


def _trend(prev_score: int | None, prev_pos: int | None,
           score: int, pos: int | None, growth: float | None, rank_delta: int | None) -> str:
    if prev_score is None:
        return "新晋"
    if growth is not None and growth >= SURGE_GROWTH:
        return "飙升"
    if rank_delta is not None and rank_delta >= SURGE_RANK:
        return "飙升"
    if growth is not None and growth <= DROP_GROWTH:
        return "回落"
    if rank_delta is not None and rank_delta <= DROP_RANK:
        return "回落"
    return "稳定"


def _pending_runs(conn, mode: str):
    if mode == "all":
        return conn.execute(
            "SELECT id, platform_code FROM collect_run WHERE status='ok' ORDER BY id"
        ).fetchall()
    # latest：每个平台取最近一次成功批次
    return conn.execute(
        "SELECT r.id, r.platform_code FROM collect_run r JOIN ("
        "  SELECT platform_code, MAX(id) AS rid FROM collect_run WHERE status='ok' GROUP BY platform_code"
        ") t ON t.platform_code=r.platform_code AND t.rid=r.id ORDER BY r.platform_code"
    ).fetchall()


def _process_run(conn, run_id: int, platform_code: str, first_seen: dict) -> tuple[int, int]:
    rows = conn.execute(
        "SELECT position, title, norm_title, hot_value, source, captured_at FROM hot_item "
        "WHERE run_id=? AND is_aggregate=0 ORDER BY position", (run_id,)
    ).fetchall()
    if not rows:
        return 0, 0

    items = [dict(r) for r in rows]
    scores, (bn, v_min, v_max) = _norm_scores(items)
    mtype = adapters.metric_type_of(platform_code)

    conn.execute("DELETE FROM word_metric WHERE run_id=?", (run_id,))
    conn.execute("DELETE FROM norm_basis WHERE run_id=?", (run_id,))
    conn.execute(
        "INSERT INTO norm_basis (run_id, platform_code, metric_type, board_n, v_min, v_max, method, created_at) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (run_id, platform_code, mtype, bn, v_min, v_max, METHOD, now_iso()),
    )

    out: list[tuple] = []
    filtered = 0
    for it in items:
        nt = it["norm_title"]
        if nt not in scores:
            continue
        title = it["title"]
        pos = it["position"]
        score = scores[nt]

        ok, reason = lexicon.check_filter(title, nt)
        category, conf = lexicon.classify(title)

        prev = conn.execute(PREV_SQL, (platform_code, nt, run_id)).fetchone()
        prev_score = prev["norm_score"] if prev else None
        prev_pos = prev["position"] if prev else None
        growth = round((score - prev_score) / prev_score, 4) if prev_score else None
        rank_delta = (prev_pos - pos) if (prev_pos is not None and pos is not None) else None
        trend = _trend(prev_score, prev_pos, score, pos, growth, rank_delta)

        first = first_seen.get((platform_code, nt)) or it["captured_at"]
        if not ok:
            filtered += 1
        out.append((
            run_id, platform_code, nt, title, mtype, it["hot_value"], pos,
            score, prev_score, growth, rank_delta, first,
            category, conf, trend, 0 if ok else 1, reason,
            it.get("source") or "raw", it["captured_at"],
        ))

    conn.executemany(INSERT_SQL, out)
    conn.commit()
    return len(out), filtered


def build(conn, mode: str = "latest") -> dict:
    """构建统一标准表 word_metric。

    mode="latest"：加工各平台最新成功批次（采集后增量执行，依赖已有历史算环比）
    mode="all"   ：清空后按批次时间序全量重建（历史回填，环比最准确）
    """
    if mode == "all":
        conn.execute("DELETE FROM word_metric")
        conn.execute("DELETE FROM norm_basis")
        conn.commit()

    first_seen = {(r["platform_code"], r["norm_title"]): r["first_seen"]
                  for r in conn.execute(FIRST_SEEN_SQL)}

    runs = _pending_runs(conn, mode)
    total = filtered = boards = 0
    for r in runs:
        n, f = _process_run(conn, r["id"], r["platform_code"], first_seen)
        if n:
            boards += 1
        total += n
        filtered += f
    return {"mode": mode, "boards": boards, "rows": total, "filtered": filtered}
