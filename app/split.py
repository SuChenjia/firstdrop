"""盘点/聚合条目的识别与拆分（数据清洗的一环）。

问题：部分榜单条目是"一题多事"的盘点栏目，例如 36氪
「8点1氪丨茶百道涉IPO欺诈，被暂停交易；苹果连夜发布3款智能家居新品…；DeepSeek将完成1000亿元融资」。
这类条目若整体入库，会造成：分类失真（整条只挂一个标签）、
跨平台对齐失效（长 blob 与任何话题都算不出相似度）、热度口径失真、时序列污染。

处理：识别为聚合条目后，按强分隔符拆成多个子话题，作为独立条目入库
（子话题热度置空，退化为纯榜位分；打 source='split' 标记并记录父条目，便于追溯）。
父条目本身保留在原始表中，但标记 is_aggregate=1，在归一化/对齐/预警中被排除。

注意：只用"强分隔符"切分（；, ;, 丨, |）。不要用「、」「，」——单个事件内部
也常用它们，会误拆。
"""
from __future__ import annotations

import re

from .align import _bigrams, _jaccard
from .normalize import normalize_title

STRONG_SEPS = ("；", ";", "丨", "|")
STRONG_SEP_MIN = 2          # 强分隔符达到该数量即判为聚合条目
MIN_SEG_LEN = 6             # 子话题最短长度（过滤碎片）
DUP_THRESHOLD = 0.6         # 子话题与该批次"单条"条目相似度≥此值即判为重复，丢弃

# 盘点栏目名（出现在标题开头即判定），比纯分隔符更可靠
COLUMN_PREFIXES = (
    "8点1氪", "早报", "晚报", "晨报", "午报", "夜读",
    "每日速览", "一周要闻", "一周热点", "盘中播报", "收盘播报", "盘前播报",
)

_PREFIX_MAX = 12            # 栏目前缀（"丨" 之前部分）的最大长度
_STRIP_CHARS = " -—·：:"


def detect(title: str) -> tuple[bool, str | None]:
    """判断是否为盘点/聚合条目，返回 (是否聚合, 理由)。"""
    if not title:
        return False, None
    for k in COLUMN_PREFIXES:
        if title.startswith(k):
            return True, f"栏目:{k}"
    strong = sum(title.count(s) for s in STRONG_SEPS)
    if strong >= STRONG_SEP_MIN:
        return True, f"强分隔符x{strong}"
    return False, None


def split_title(title: str) -> list[str]:
    """把聚合标题拆成子话题列表（已剥栏目前缀、去碎片）。"""
    body = title
    if "丨" in body:
        head, rest = body.split("丨", 1)
        if len(head) <= _PREFIX_MAX:     # 只有短前缀才当栏目头剥掉
            body = rest
    body = body.replace(";", "；").replace("|", "丨")
    parts = re.split(r"[；丨]", body)
    return [p.strip(_STRIP_CHARS) for p in parts if len(p.strip(_STRIP_CHARS)) >= MIN_SEG_LEN]


def _target_runs(conn, mode: str):
    if mode == "all":
        return conn.execute(
            "SELECT id, platform_code FROM collect_run WHERE status='ok' ORDER BY id"
        ).fetchall()
    return conn.execute(
        "SELECT r.id, r.platform_code FROM collect_run r JOIN ("
        "  SELECT platform_code, MAX(id) AS rid FROM collect_run WHERE status='ok' GROUP BY platform_code"
        ") t ON t.platform_code=r.platform_code AND t.rid=r.id ORDER BY r.platform_code"
    ).fetchall()


_CHILD_INSERT = """
INSERT INTO hot_item
  (run_id, platform_code, position, is_top, title, norm_title, external_id, captured_at,
   source, parent_norm_title, seg_index)
VALUES (?,?,?,0,?,?,?,?, 'split', ?, ?)
"""


def _is_dup_of_standalone(nt: str, gram, raw_refs: list[tuple[str, set]]) -> bool:
    """子话题是否与该批次内的"单条"条目重复（等价或高相似）。"""
    for rnt, rgram in raw_refs:
        if nt == rnt:
            return True
        if _jaccard(gram, rgram) >= DUP_THRESHOLD:
            return True
    return False


def expand(conn, mode: str = "latest") -> dict:
    """识别聚合条目并拆出子话题，写入 hot_item（source='split'）。

    幂等：每次先清除目标批次已有的拆分产物，再重算。
    mode="latest" 只处理各平台最新成功批次；mode="all" 处理全部成功批次。
    """
    runs = _target_runs(conn, mode)
    n_agg = n_child = n_drop = 0
    for r in runs:
        run_id, plat = r["id"], r["platform_code"]

        # 清理该批次上次的拆分产物与标记，保证幂等
        conn.execute("DELETE FROM hot_item WHERE run_id=? AND source='split'", (run_id,))
        conn.execute(
            "UPDATE hot_item SET is_aggregate=0, aggregate_reason=NULL "
            "WHERE run_id=? AND source='raw'", (run_id,)
        )

        rows = [dict(x) for x in conn.execute(
            "SELECT id, position, title, norm_title, external_id, captured_at "
            "FROM hot_item WHERE run_id=? AND source='raw'", (run_id,)
        )]

        # 先识别聚合条目；同时收集同批次"单条"条目（非聚合）作为去重参照
        raw_refs: list[tuple[str, set]] = []
        for row in rows:
            is_agg, reason = detect(row["title"])
            row["_agg"] = is_agg
            if is_agg:
                n_agg += 1
                conn.execute(
                    "UPDATE hot_item SET is_aggregate=1, aggregate_reason=? WHERE id=?",
                    (reason, row["id"]),
                )
            else:
                raw_refs.append((row["norm_title"], _bigrams(row["norm_title"])))

        children: list[tuple] = []
        for row in rows:
            if not row["_agg"]:
                continue
            for i, seg in enumerate(split_title(row["title"]), 1):
                nt = normalize_title(seg)
                if not nt:
                    continue
                # 去重：同批次内已有等价"单条"条目时丢弃该子话题，保留带真实热度的那条
                if _is_dup_of_standalone(nt, _bigrams(nt), raw_refs):
                    n_drop += 1
                    continue
                ext = f"{row['external_id']}#{i}" if row["external_id"] else None
                children.append((run_id, plat, row["position"], seg, nt, ext,
                                 row["captured_at"], row["norm_title"], i))
        if children:
            conn.executemany(_CHILD_INSERT, children)
            n_child += len(children)

    conn.commit()
    return {"aggregates": n_agg, "children": n_child, "dropped": n_drop}
