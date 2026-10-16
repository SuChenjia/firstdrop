"""别名词典自动挖掘：把候选别名对写入 entity_alias_suggest（pending，待人工审核）。

三种挖掘来源：
  1. 句式抽取：从原始标题里抽 "全称（简称）/ X简称Y / X又称Y" 等显式句式（高置信）；
  2. extid 同源：同平台 + 同 external_id 但 norm_title 不同 → 同一内容的改题，作等价对；
  3. 同簇共现：同一话题簇内的成员互相比对，剥离公共前后缀后取"上下文相同、
     中间差异片段"作候选（如 我军↔解放军 这类"简称 vs 全称"形态）。

候选先落 pending，人工审核后再进正式词典 entity_alias（enabled=1）；本模块不自动启用。
"""
from __future__ import annotations

import re

from . import align
from .normalize import normalize_title

MIN_PAIR_LEN = 2
MAX_ALIAS_LEN = 8                    # 别名（短）上限
MAX_CANON_LEN = 24                   # 规范词（可较长，如"中国人民政治协商会议"）上限
DIFF_MAX_LEN = 4                     # 差异片段（模糊挖掘）允许的最大长度
MAX_ROWS = 500                       # 单次最多写入的候选数，防爆量
IMPACT_MAX = 100                     # 单次最多评估影响的候选数（每个候选 = 一次反事实对齐，控开销）

# 非实体用字：助词/副词/时间词/常见虚词。含这些字的片段大概率是措辞差异而非别名，
# 用于过滤「媒体评↔官方通报」「将开通↔明日正式开通」这类噪音。
# 注意：不含"我/你/他"等代词，否则会误杀「我军→解放军」这类真正的简称。
_NON_ENTITY_CHARS = set(
    "了的是在就已未还又再更最很都也而被把让向从对于与和及或各该此其之这那"
    "明日今昨天年周时分秒将正数式开通评通报曝回应"
)

_PATTERNS = [
    # 全称（简称）/ 全称(简称)
    (re.compile(r"([\u4e00-\u9fa5A-Za-z0-9]{2,14})[（(]([\u4e00-\u9fa5A-Za-z0-9]{2,14})[)）]"),
     "paren"),
    # X简称Y：Y 是 X 的简称
    (re.compile(r"([\u4e00-\u9fa5A-Za-z0-9]{2,14})简称([\u4e00-\u9fa5A-Za-z0-9]{2,14})"),
     "jiancheng"),
    # X又称/又名/亦称/即/被称为Y
    (re.compile(r"([\u4e00-\u9fa5A-Za-z0-9]{2,14})(?:又称|又名|亦称|即|被称为)([\u4e00-\u9fa5A-Za-z0-9]{2,14})"),
     "equal"),
]

# 交叉语种括号（中文全称 ↔ 拉丁简称/英译，如 瑞银（UBS）、阿斯麦（ASML））：
# 两侧无共同汉字，无法走 _overlap 路径，单独识别。
_LATIN_TOKEN = re.compile(r"^(?=.*[A-Za-z])[A-Za-z0-9.&\-]{2,12}$")   # 纯拉丁/数字，含字母
_CJK_ONLY = re.compile(r"^[\u4e00-\u9fa5]{2,14}$")                    # 纯汉字
# 交叉语种无汉字锚点可裁边，改在最后一个边界/功能字处切分中文全称（「摩根大通将阿斯麦」→「阿斯麦」）。
_BOUNDARY_CHARS = set("将把被对向给称报等与和及或的")


def _norm(s: str) -> str:
    return normalize_title(s or "")


def _has_non_entity(tok: str) -> bool:
    return any(ch in _NON_ENTITY_CHARS for ch in tok)


def _trim_left(full: str, short: str) -> str:
    """裁掉全称左侧的上下文，直到首字与简称有交集（如"目标推进会召开碳达峰碳中和"→"碳达峰碳中和"）。"""
    s = set(short)
    i = 0
    while i < len(full) and full[i] not in s:
        i += 1
    return full[i:]


def _trim_right(short: str, full: str) -> str:
    """裁掉简称右侧的上下文，直到末字与全称有交集（如"上交所今日收盘"→"上交所"）。"""
    s = set(full)
    j = len(short)
    while j > 0 and short[j - 1] not in s:
        j -= 1
    return short[:j]


def _overlap(a: str, b: str) -> bool:
    """两侧至少共享一个字才算"缩写关系"（挡掉"强度 vs 绝大部分的游戏"这类无关括号内容）。"""
    return bool(set(a) & set(b))


def _trim_cjk_context(full: str) -> str:
    """剥掉中文全称左侧的上下文词（如「摩根大通将阿斯麦」→「阿斯麦」）。

    交叉语种没有共同汉字可作锚点，改在最后一个边界/功能字处切分；无边界字则原样返回。
    """
    idx = -1
    for i, ch in enumerate(full):
        if ch in _BOUNDARY_CHARS:
            idx = i
    return full[idx + 1:] if idx >= 0 else full


def _cross_script_pair(g1: str, g2: str):
    """交叉语种括号对：一侧为拉丁简称/英译、另一侧为纯中文全称。

    返回 (alias, canonical) 或 None：拉丁侧作别名，中文侧剥掉左侧上下文作规范词。
    如「瑞银（UBS）」→ ubs→瑞银，「摩根大通将阿斯麦（ASML）」→ asml→阿斯麦。
    纯英文全称（如 Masan High-Tech Materials）含空格，不匹配 _LATIN_TOKEN，会被排除。
    """
    latin = cjk = None
    if _LATIN_TOKEN.match(g2) and _CJK_ONLY.match(g1):
        latin, cjk = g2, g1
    elif _LATIN_TOKEN.match(g1) and _CJK_ONLY.match(g2):
        latin, cjk = g1, g2
    if not latin:
        return None
    return _ok_pair(latin, _trim_cjk_context(cjk), guard_substr=False)


def _ok_pair(alias: str, canon: str, guard_substr: bool = True):
    """校验并归一化候选对，返回 (alias_norm, canonical_norm) 或 None。"""
    a, c = _norm(alias), _norm(canon)
    if not a or not c or a == c:
        return None
    if not (MIN_PAIR_LEN <= len(a) <= MAX_ALIAS_LEN):
        return None
    if not (MIN_PAIR_LEN <= len(c) <= MAX_CANON_LEN):
        return None
    if guard_substr and (a in c or c in a):
        # 子串关系（如 北京 vs 北京师范大学）易误合并，模糊挖掘时跳过
        return None
    return a, c


def _diff_pairs(t1: str, t2: str) -> list[tuple[str, str]]:
    """对齐公共前后缀：只取"上下文相同、中间差异片段"的一对作候选。

    做法：剥离两标题的公共前缀 P 与公共后缀 S，得到差异中段 X、Y。仅当
    X、Y 均非空、长度不等、较长者不超过 DIFF_MAX_LEN、且两侧都不含非实体
    用字时，才输出候选（短者为别名，长者为规范词）。

    额外处理"共享边界字"：当两实体以同一字收尾且其后紧跟公共尾巴时（如
    「我|军匕首枪…」vs「解放|军匕首枪…」），公共后缀会把边界字"军"吞掉，
    只剩"我"（1 字）导致漏判。此时把公共后缀的首字回填到两侧中段各一次
    （→"我军"/"解放军"）作为附加候选；因要求两侧长度仍不等，天然的
    "枚举下标"式差异（如 陆军/海军，回填后等长）仍会被正确排除。

    相比按最长公共子串左右截取，这里用"公共上下文约束"代替"差异片段长度约束"：
    只有共享足够前后缀、仅中间一小段替换的标题才可能是同实体别名；措辞差异
    （如 明日正式开通 vs 将开通）因含虚词或长度不符被挡在门外。
    """
    if not t1 or not t2 or t1 == t2:
        return []
    p = 0
    while p < len(t1) and p < len(t2) and t1[p] == t2[p]:
        p += 1
    s = 0
    while (s < len(t1) - p and s < len(t2) - p
           and t1[len(t1) - 1 - s] == t2[len(t2) - 1 - s]):
        s += 1
    x, y = t1[p:len(t1) - s], t2[p:len(t2) - s]
    suffix = t1[len(t1) - s:] if s else ""

    cands = [(x, y)]
    if x and y and suffix:                       # 回填共享边界字
        cands.append((x + suffix[0], y + suffix[0]))

    out: list[tuple[str, str]] = []
    for a, b in cands:
        if not a or not b or len(a) == len(b):
            continue
        if not (MIN_PAIR_LEN <= min(len(a), len(b)) and max(len(a), len(b)) <= DIFF_MAX_LEN):
            continue
        pair = _ok_pair(*sorted((a, b), key=len))
        if pair and not _has_non_entity(pair[0]) and not _has_non_entity(pair[1]):
            out.append(pair)
    return out


def _pattern_pairs(title: str) -> list[tuple[str, str, str]]:
    """从单条标题抽取句式对，返回 [(alias, canonical, kind)]（纯函数，便于测试）。

    正则捕获天然会多吞上下文（如「上海证券交易所简称上交所今日收盘」里简称后
    黏了「今日收盘」）。这里统一用"与另一侧取交集"的方式裁掉两端多余上下文：
    全称左侧裁到首字与简称相交，简称右侧裁到末字与全称相交；再要求两侧至少
    共享一个字（缩写关系），滤掉「（强度）」这类与前置语无关的括号内容。
    """
    out: list[tuple[str, str, str]] = []
    for pat, kind in _PATTERNS:
        for m in pat.finditer(title):
            g1, g2 = _norm(m.group(1)), _norm(m.group(2))
            if not g1 or not g2:
                continue
            # 交叉语种括号（中文全称 ↔ 拉丁简称/英译）单独处理：两侧无共同汉字，走不了 _overlap。
            cs = _cross_script_pair(g1, g2)
            if cs:
                out.append((cs[0], cs[1], kind))
                continue
            # 长短分配：长者为全称/规范词，短者为简称/别名（对三种句式统一适用，
            # 兼容"简称（全称）"这种倒置写法）。
            full, short = (g1, g2) if len(g1) >= len(g2) else (g2, g1)
            full = _trim_left(full, short)
            short = _trim_right(short, full)
            if not full or not short or not _overlap(full, short):
                continue
            pair = _ok_pair(short, full, guard_substr=False)
            if pair:
                out.append((pair[0], pair[1], kind))
    return out


def mine_pattern(conn) -> list[dict]:
    """句式抽取：括号式「全称（简称）」/「X简称Y」/「X又称·又名·即Y」。"""
    out: list[dict] = []
    for r in conn.execute("SELECT DISTINCT title FROM hot_item WHERE source='raw'"):
        t = r["title"]
        for alias, canon, kind in _pattern_pairs(t):
            out.append({"alias": alias, "canonical": canon, "source": "句式抽取",
                        "evidence": f"{kind}: {t[:44]}"})
    return out


def mine_extid(conn) -> list[dict]:
    """同平台 + 同 external_id 的不同标题 → 同一内容的改题（等价对）。"""
    out: list[dict] = []
    groups = conn.execute(
        "SELECT platform_code, external_id, COUNT(DISTINCT norm_title) AS n "
        "FROM hot_item WHERE source='raw' AND external_id IS NOT NULL AND external_id <> '' "
        "GROUP BY platform_code, external_id HAVING n >= 2"
    ).fetchall()
    for g in groups:
        rows = conn.execute(
            "SELECT norm_title, title, COUNT(*) c FROM hot_item "
            "WHERE platform_code=? AND external_id=? AND source='raw' "
            "GROUP BY norm_title ORDER BY c DESC, LENGTH(norm_title) DESC",
            (g["platform_code"], g["external_id"]),
        ).fetchall()
        if len(rows) < 2:
            continue
        rep = rows[0]                              # 出现最多/最长的作代表
        for v in rows[1:]:
            for a, c in _diff_pairs(rep["norm_title"], v["norm_title"]):
                out.append({"alias": a, "canonical": c, "source": "extid同源",
                            "evidence": f"{g['platform_code']}/{g['external_id']}: "
                                        f"{rep['title'][:22]} ↔ {v['title'][:22]}"})
    return out


def mine_cluster(conn) -> list[dict]:
    """同一话题簇内成员两两比对，抽取差异片段作候选。"""
    out: list[dict] = []
    groups: dict[int, list[tuple[str, str]]] = {}
    for r in conn.execute("SELECT cluster_id, norm_title, title FROM topic_cluster"):
        groups.setdefault(r["cluster_id"], []).append((r["norm_title"], r["title"]))
    for cid, members in groups.items():
        if len(members) < 2:
            continue
        for i in range(len(members)):
            for j in range(i + 1, len(members)):
                for a, c in _diff_pairs(members[i][0], members[j][0]):
                    out.append({"alias": a, "canonical": c, "source": "同簇共现",
                                "evidence": f"簇{cid}: {members[i][1][:22]} ↔ {members[j][1][:22]}"})
    return out


def _score_impacts(conn) -> int:
    """为待审候选计算"跨平台影响"（反事实评估），写入 impact_topics / impact_platforms。

    影响口径：把该候选 (alias→canonical) 注入词典后，有多少话题被并入他簇、
    覆盖多少平台（见 align.alias_impact）。0/0 = 当前对跨平台聚合无影响——
    此类候选按 Q1-B 保留但归入低优先级，不丢弃。
    """
    rows = conn.execute(
        "SELECT alias_norm, canonical FROM entity_alias_suggest "
        "WHERE status IN ('pending','approved') ORDER BY created_at LIMIT ?", (IMPACT_MAX,)
    ).fetchall()
    if not rows:
        return 0
    base_sig = align.cluster_signature(conn)          # 基线只算一次，供全部候选复用
    n = 0
    for r in rows:
        im = align.alias_impact(conn, r["alias_norm"], r["canonical"], base_sig=base_sig)
        conn.execute(
            "UPDATE entity_alias_suggest SET impact_topics=?, impact_platforms=? "
            "WHERE alias_norm=? AND canonical=?",
            (im["topics"], im["platforms"], r["alias_norm"], r["canonical"]),
        )
        n += 1
    conn.commit()
    return n


def run(conn) -> dict:
    """执行三种挖掘，候选写入 entity_alias_suggest（pending），返回统计。"""
    existing = {r["alias_norm"] for r in conn.execute("SELECT alias_norm FROM entity_alias")}

    cands = mine_pattern(conn) + mine_extid(conn) + mine_cluster(conn)
    rows: list[tuple] = []
    seen: set[tuple] = set()
    for c in cands:
        a, cc = _norm(c["alias"]), _norm(c["canonical"])
        if not a or not cc or a == cc or a in existing:
            continue
        key = (a, cc)
        if key in seen:
            continue
        seen.add(key)
        rows.append((a, cc, c["source"], (c["evidence"] or "")[:200]))
        if len(rows) >= MAX_ROWS:
            break

    before = conn.execute(
        "SELECT COUNT(*) FROM entity_alias_suggest WHERE status='pending'").fetchone()[0]
    if rows:
        conn.executemany(
            "INSERT OR IGNORE INTO entity_alias_suggest (alias_norm, canonical, source, evidence) "
            "VALUES (?,?,?,?)", rows)
        conn.commit()
    after = conn.execute(
        "SELECT COUNT(*) FROM entity_alias_suggest WHERE status='pending'").fetchone()[0]

    scored = _score_impacts(conn)
    active = conn.execute(
        "SELECT COUNT(*) FROM entity_alias_suggest WHERE status='pending' "
        "AND impact_topics > 0").fetchone()[0]

    by_source: dict[str, int] = {}
    for r in conn.execute("SELECT source, COUNT(*) c FROM entity_alias_suggest "
                          "WHERE status='pending' GROUP BY source"):
        by_source[r["source"]] = r["c"]
    return {"scanned": len(cands), "new": after - before, "pending_total": after,
            "impact_scored": scored, "impact_active": active, "by_source": by_source}
