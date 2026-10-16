"""跨平台实体归一 + 同词融合：把同一实体的多平台热度合成一个聚合维度。

前置：align.py 已把各平台描述同一事件的话题聚成一个簇（cluster_id）。
本模块在簇内，用"归一化热度 norm_score"（word_metric，0~10000，跨平台可比）
融合出实体级聚合热度，落表 entity_metric（快照，每次加工整体刷新）。

两种融合口径并列产出，便于对比：
  设各平台归一化热度为 s_i，覆盖平台数为 k，扩散系数为 K
  · 峰值主导式  agg_peak  = clamp(max(s_i) + K·ln(1 + (k-1)), 0, 10000)
      以最热平台强度为基准，跨平台扩散按边际递减加成；稳健、可解释。
  · 联合曝光式  agg_joint = clamp(10000·(1 - ∏(1 - s_i/10000)), 0, 10000)
      多平台同时出现即"联合曝光"，多个中等热度平台会显著抬升。
  单平台实体（k=1）时两式均退化为该平台的 norm_score。

口径与公式全部落表留痕（max_norm_score / spread_bonus 可复算）。
"""
from __future__ import annotations

import math

from .db import now_iso

SPREAD_K = 800.0   # 扩散加成系数：k=2 时约 +554，k=3 约 +879，边际递减
CAP = 10000

# 取各平台"最新批次"的归一化热度，与 topic_cluster 对齐
MEMBER_SQL = """
SELECT tc.cluster_id, tc.platform_code, tc.norm_title, tc.title,
       w.norm_score, w.position, w.category, w.trend_flag, w.first_seen_at
FROM topic_cluster tc
LEFT JOIN (
  SELECT wm.platform_code, wm.norm_title, wm.norm_score, wm.position,
         wm.category, wm.trend_flag, wm.first_seen_at
  FROM word_metric wm
  WHERE wm.run_id = (
    SELECT MAX(x.run_id) FROM word_metric x WHERE x.platform_code = wm.platform_code
  )
) w ON w.platform_code = tc.platform_code AND w.norm_title = tc.norm_title
"""


def _clamp(v: float) -> int:
    return int(min(max(round(v), 0), CAP))


def fuse(conn) -> dict:
    """在簇内融合各平台归一化热度，刷新 entity_metric 快照。"""
    rows = conn.execute(MEMBER_SQL).fetchall()
    if not rows:
        conn.execute("DELETE FROM entity_metric")
        conn.commit()
        return {"entities": 0, "multi": 0}

    # 按簇聚合：收集平台 -> 该平台最佳成员（norm_score 最高）
    clusters: dict[int, dict] = {}
    for r in rows:
        cid = r["cluster_id"]
        c = clusters.setdefault(cid, {"members": [], "best": None})
        c["members"].append(r)
        if r["norm_score"] is not None:
            if c["best"] is None or r["norm_score"] > c["best"]["norm_score"]:
                c["best"] = r

    ts = now_iso()
    out: list[tuple] = []
    multi = 0
    for cid, c in clusters.items():
        # 每个平台取该平台在簇内的最高归一化热度（避免同平台多词重复计分）
        per_platform: dict[str, int] = {}
        for m in c["members"]:
            if m["norm_score"] is None:
                continue
            pc = m["platform_code"]
            per_platform[pc] = max(per_platform.get(pc, 0), m["norm_score"])

        scores = list(per_platform.values())
        k = len(per_platform)
        platforms = ",".join(sorted(per_platform))

        if k >= 2:
            multi += 1

        if k == 0:
            # 簇内成员均无归一化热度（极端兜底）
            best = c["members"][0]
            out.append((cid, best["title"], None, 0, 0, 0, platforms, 0, 0.0,
                        _min_pos(c["members"]), best["first_seen_at"], best["trend_flag"], ts))
            continue

        max_norm = max(scores)
        spread_bonus = round(SPREAD_K * math.log(1 + (k - 1)), 1)
        agg_peak = _clamp(max_norm + spread_bonus)
        # 联合曝光：1 - ∏(1 - s_i/10000)
        joint = 1.0
        for s in scores:
            joint *= (1 - s / CAP)
        agg_joint = _clamp(CAP * (1 - joint))

        best = c["best"]
        out.append((
            cid, best["title"], best["category"], agg_peak, agg_joint, k, platforms,
            max_norm, spread_bonus, _min_pos(c["members"]),
            _min_first(c["members"]), best["trend_flag"], ts,
        ))

    conn.execute("DELETE FROM entity_metric")
    conn.executemany(
        "INSERT INTO entity_metric (entity_id, canonical_title, category, agg_peak, agg_joint, "
        "platform_count, platforms, max_norm_score, spread_bonus, best_position, first_seen_at, "
        "trend_flag, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        out,
    )
    conn.commit()
    return {"entities": len(clusters), "multi": multi}


def _min_pos(members) -> int | None:
    vals = [m["position"] for m in members if m["position"] is not None]
    return min(vals) if vals else None


def _min_first(members) -> str | None:
    vals = [m["first_seen_at"] for m in members if m["first_seen_at"]]
    return min(vals) if vals else None
