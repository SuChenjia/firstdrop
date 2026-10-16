"""实体别名归一：把同义表述改写成规范表述，提升跨平台同词合并召回。

为什么要做：bigram 相似度对"同义但不同措辞"的标题无能为力。
例如「我军匕首枪曝光」vs「解放军演示匕首枪」的 Jaccard 仅 0.18（低于阈值 0.4），
把「我军」改写成「解放军」后升到 0.40，正好越过阈值被合并为同一实体。

用法（在 bigram 对齐之前）：
    rules, canonicals = load_alias_map(conn)
    key, hits = rewrite(norm_title, rules, canonicals)
    # 用 key 替代 norm_title 参与 bigram/Jaccard 相似度计算
"""
from __future__ import annotations

import re

ANCHOR_SEP = "\x01"          # 规范锚点分隔符（控制字符，正常标题不会出现）
_ASCII = re.compile(r"^[a-z0-9]+$")


def make_rule(alias: str, canon: str) -> tuple[str, str, re.Pattern | None]:
    """把一对 (别名, 规范词) 编译为改写规则。

    ASCII 别名（如 ai/nba）编译为带词边界的正则，避免 "ai" 命中 "baidu"；
    中文别名走朴素子串替换（pat=None）。供 load_alias_map 与反事实评估
    （临时注入一条词典规则）共用，保证两条路径口径一致。
    """
    pat = (re.compile(r"(?<![a-z0-9])" + re.escape(alias) + r"(?![a-z0-9])")
           if _ASCII.match(alias) else None)
    return (alias, canon, pat)


def build_rules(pairs) -> list[tuple[str, str, re.Pattern | None]]:
    """从 (alias, canonical) 序列构建规则列表（按别名长度降序，长别名优先）。"""
    rules = [make_rule(a, c) for a, c in pairs if a and c]
    rules.sort(key=lambda x: len(x[0]), reverse=True)
    return rules


def load_alias_map(conn) -> tuple[list[tuple[str, str, re.Pattern | None]], set[str]]:
    """读取启用的别名词典。

    返回 (规则列表, 规范词集合)。规则已按别名长度降序排列，保证
    「联合国安理会」先于「安理会」被匹配，避免长别名被短别名截断。
    """
    rows = conn.execute(
        "SELECT alias_norm, canonical FROM entity_alias WHERE enabled=1"
    ).fetchall()
    rules = build_rules([(r["alias_norm"], r["canonical"]) for r in rows])
    canonicals = {c for _, c, _ in rules}
    return rules, canonicals


def rewrite(norm_title: str, rules, canonicals: set[str]) -> tuple[str, list[str]]:
    """把标题里的别名替换为规范词，并注入规范锚点。

    返回 (改写后的实体键, 命中的别名列表)。命中同一个规范词的标题会共享
    锚点 token 的 bigram，给合并留出冗余（实测把 0.40 抬到 0.47）。
    """
    t = norm_title
    hits: list[str] = []
    for alias, canon, pat in rules:
        if pat is not None:
            if pat.search(t):
                t = pat.sub(canon, t)
                hits.append(alias)
        elif alias in t:
            t = t.replace(alias, canon)
            hits.append(alias)

    # 规范锚点：命中任一规范词即追加，让"共享同一实体"的标题必然共享 bigram
    canon_hits = sorted({c for c in canonicals if c in t})
    if canon_hits:
        t = t + ANCHOR_SEP + ANCHOR_SEP.join(canon_hits) + ANCHOR_SEP
    return t, hits
