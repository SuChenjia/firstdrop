"""跨平台话题对齐：把不同平台上描述同一事件的话题聚合为"话题簇"。

算法（轻量、零第三方依赖）：
  0. 实体别名归一（alias.rewrite）：把同义表述改写成规范表述，再进入相似度计算；
  1. 对（改写后的）实体键做字符 bigram 集合；
  2. 用 bigram 倒排索引生成候选对，计算 Jaccard 相似度；
  3. 相似度达阈值即用并查集合并成一个簇；同平台同 external_id 直接判同；
  4. 簇 id 增量复用：新簇若与旧快照有成员重叠，则沿用旧簇 id，保证预警与页面引用连续。

对齐只覆盖"各平台最新一次成功采集"的榜单（v_latest_item），即当前快照。

三条合并通道（任一满足即合并）：
  A. bigram-Jaccard ≥ threshold（同措辞/近似措辞）；
  B. 长公共子串兜底（_lcs_rule，见 #23）——挡掉长标题稀释 Jaccard 的漏合；
  C. 实体约束 + 语义重合（_entity_rule，见 #24）——两标题共享一个"具体实体 span"
     且整体字符重合度达标的，判为同一事件。用于修复"酷似↔撞脸"这类**谓词差异**
     （谓词差异不能靠词典，只能靠匹配算法）。
"""

from __future__ import annotations

import difflib

from .alias import build_rules, load_alias_map, rewrite
from .db import now_iso


def _bigrams(text: str) -> set[str]:
    if not text:
        return set()
    if len(text) < 2:
        return {text}
    return {text[i:i + 2] for i in range(len(text) - 1)}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    return inter / (len(a) + len(b) - inter)


# 长公共子串兜底：长标题会稀释 bigram-Jaccard，导致同一事件在各平台的不同措辞被切开
# （例："工人撞脸陈小春…(13字)" 与 "撞脸陈小春工人…(11字)" J≈0.25，但共享"撞脸陈小春"）。
# 两标题存在"足够长且足够特异"的公共子串时，判定为同一事件。
# 注意：本规则作用在「原始 norm_title」上，而非别名改写后的实体键。改写键会追加
# 规范锚点后缀（alias.ANCHOR_SEP + 规范词），该后缀对所有命中同一规范词的标题都相同
# （如凡出现 "ai" 者末尾都带 "人工智能\x01"），是人为共享片段、特异性弱，不应作为
# 跨事件合并依据；只有自然标题文本里的长公共子串才具备事件辨识度。
_LCS_MIN = 5           # 公共子串最短长度
_LCS_CJK_MIN = 3       # 公共子串至少含 3 个汉字：挡掉 openai/anthropic 等纯外文品牌名导致的跨事件误合
_LCS_RATIO_MIN = 0.35  # 公共子串占较短标题的比例下限：挡掉"如何评价/指数日内跌"等通用框定语稀释出的误合

# ---- 实体约束通道（#24）----
# 判据：两条标题共享一个"具体实体 span"（人名/机构/产品/事件专名），
# 且整体「字符集 Jaccard」达标 → 判为同一事件。
# 为什么用字符集而非 bigram：谓词差异（酷似↔撞脸）会破坏 bigram 连续性，
# 但事件核心实体与关键名词（工人/长相/陈小春）的字符仍在，字符集重合度稳健。
# 由于 Q2-B 规定"不允许谓词同义词进词典"，谓词差异只能在此通道（匹配算法）里消化。
_ENT_MIN = 3                # 共享实体 span 最短长度
_ENT_CHAR_JACCARD = 0.40    # 语义重合度阈值（字符集 Jaccard）
# 通用/功能字/高频谓词：出现在 span 里说明该 span 是"通用框定语"而非专名，
# 不能作为实体证据（如 "为什么""什么样的""正式开通""发现回应"）。
_GENERIC_CHARS = set(
    "了的是在就已未还又再更最很都也而被把让向从对于与和及或各该此其之这那就"
    "什为么样怎吗呢否认同称说正式开通发现回应表示认为媒评通报曝去"
    "涨跌崩黄牛"
)


def _cjk_count(s: str) -> int:
    return sum(1 for ch in s if "\u4e00" <= ch <= "\u9fa5")


def _lcs(a: str, b: str) -> tuple[int, str]:
    """最长公共(连续)子串的长度与内容。"""
    m = difflib.SequenceMatcher(None, a, b, autojunk=False).find_longest_match(0, len(a), 0, len(b))
    return m.size, a[m.a:m.a + m.size]


def _lcs_rule(a: str, b: str) -> bool:
    """长公共子串合并判据。子串需特异：含汉字≥_LCS_CJK_MIN 且不含数字。

    含数字的公共子串多为数量/指标表述（如"5万亿美元""5年期国债收益率下行"，各国/主体通用），
    特异性弱、跨主体易误合，故排除。
    """
    if not a or not b:
        return False
    size, s = _lcs(a, b)
    if size < _LCS_MIN:
        return False
    if _cjk_count(s) < _LCS_CJK_MIN:
        return False
    if any(ch.isdigit() for ch in s):
        return False
    return size >= _LCS_RATIO_MIN * min(len(a), len(b))


def _char_jaccard(a: str, b: str) -> float:
    """字符集 Jaccard：对词序不敏感，用于度量"剥离谓词差异后的语义重合度"。"""
    sa, sb = set(a), set(b)
    if not sa or not sb:
        return 0.0
    inter = len(sa & sb)
    return inter / (len(sa) + len(sb) - inter)


def _specific_span(a: str, b: str) -> str | None:
    """提取两标题共享的"具体实体 span"（最长公共子串里最具辨识度的一段）。

    返回 span 或 None。要求：长度 ≥ _ENT_MIN、不含数字、不含通用/功能字、
    至少 2 个汉字。避免把"指数日内跌""如何评价"这类通用框定语当成实体。
    """
    if not a or not b:
        return None
    size, s = _lcs(a, b)
    if size < _ENT_MIN:
        return None
    if any(ch.isdigit() for ch in s):
        return None
    if any(ch in _GENERIC_CHARS for ch in s):
        return None
    if _cjk_count(s) < 2:
        return None
    return s


def _entity_rule(a: str, b: str) -> bool:
    """实体约束 + 语义重合判据：共享具体实体 span，且整体字符重合度达标。"""
    if _specific_span(a, b) is None:
        return False
    return _char_jaccard(a, b) >= _ENT_CHAR_JACCARD


def compute_clusters(conn, *, threshold: float = 0.4, min_shared: int = 2,
                     extra_aliases=None) -> dict:
    """纯计算：把当前快照的话题聚成簇，**不写库**。

    真实对齐（align）与反事实评估（评估某别名若启用的效果，见 mine.py）共用本函数，
    保证两条路径的口径逐字一致。

    extra_aliases: [(alias, canonical)]，临时追加进别名词典（仅本次计算生效），
                   用于"若该别名启用会怎样"的反事实评估。

    返回 {"topics": [...], "groups": {root: [idx,...]}, "grams": [...], "alias_hits": int}
      topics[i] = (platform_code, norm_title, title, external_id, 改写键, 命中别名)
    """
    rows = conn.execute(
        "SELECT platform_code, norm_title, title, external_id FROM v_latest_item "
        "WHERE is_aggregate=0"
    ).fetchall()

    # 实体别名归一：把同义表述改写成规范表述，再算 bigram 相似度
    rules, canonicals = load_alias_map(conn)
    if extra_aliases:
        extra = build_rules(extra_aliases)
        rules = build_rules([(a, c) for a, c, _ in rules] + [(a, c) for a, c, _ in extra])
        canonicals = canonicals | {c for _, c, _ in extra}

    topics = []
    alias_hits = 0
    for r in rows:
        if not r["norm_title"]:
            continue
        key, hits = rewrite(r["norm_title"], rules, canonicals)
        if hits:
            alias_hits += 1
        topics.append((r["platform_code"], r["norm_title"], r["title"],
                       r["external_id"], key, ",".join(hits) or None))
    n = len(topics)
    if n == 0:
        return {"topics": [], "groups": {}, "grams": [], "alias_hits": 0}

    grams = [_bigrams(t[4]) for t in topics]

    # 倒排索引：bigram -> 话题下标，仅在同 bigram 内两两比较，避免 O(n^2)
    index: dict[str, list[int]] = {}
    for i, g in enumerate(grams):
        for bg in g:
            index.setdefault(bg, []).append(i)

    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    # external_id 硬规则：同平台 + 同 external_id 视为同一实体，直接合并
    # （知乎同 question、头条同 ClusterId 等平台内唯一 id）
    ext_index: dict[tuple[str, str], list[int]] = {}
    for i, t in enumerate(topics):
        if t[3]:
            ext_index.setdefault((t[0], t[3]), []).append(i)
    for idxs in ext_index.values():
        for j in idxs[1:]:
            union(idxs[0], j)

    seen_pairs: set[tuple[int, int]] = set()
    for idxs in index.values():
        m = len(idxs)
        if m < 2:
            continue
        for ii in range(m):
            for jj in range(ii + 1, m):
                a, b = idxs[ii], idxs[jj]
                if a > b:
                    a, b = b, a
                if (a, b) in seen_pairs:
                    continue
                seen_pairs.add((a, b))
                if len(grams[a] & grams[b]) < min_shared:
                    continue
                if _jaccard(grams[a], grams[b]) >= threshold:
                    union(a, b)
                # 兜底通道均作用在原始标题（t[1]），不用带锚点后缀的改写键（t[4]）
                elif _lcs_rule(topics[a][1], topics[b][1]):
                    union(a, b)
                elif _entity_rule(topics[a][1], topics[b][1]):
                    union(a, b)

    groups: dict[int, list[int]] = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)
    return {"topics": topics, "groups": groups, "grams": grams, "alias_hits": alias_hits}


def align(conn, threshold: float = 0.4, min_shared: int = 2) -> dict:
    """执行一次对齐并刷新 topic_cluster 快照，返回统计信息。"""
    res = compute_clusters(conn, threshold=threshold, min_shared=min_shared)
    topics, groups, grams = res["topics"], res["groups"], res["grams"]
    alias_hits = res["alias_hits"]
    n = len(topics)
    if n == 0:
        conn.execute("DELETE FROM topic_cluster")
        conn.commit()
        return {"topics": 0, "clusters": 0, "multi": 0, "alias_hits": 0}

    # 旧快照 -> 复用簇 id
    old = {
        (r["platform_code"], r["norm_title"]): r["cluster_id"]
        for r in conn.execute("SELECT platform_code, norm_title, cluster_id FROM topic_cluster")
    }
    next_id = (max(old.values()) + 1) if old else 1
    used: set[int] = set()

    ts = now_iso()
    mapping_rows: list[tuple] = []
    multi = 0
    for members in groups.values():
        # 投票：新簇优先沿用成员中出现次数最多的旧簇 id
        vote: dict[int, int] = {}
        for i in members:
            cid = old.get((topics[i][0], topics[i][1]))
            if cid is not None:
                vote[cid] = vote.get(cid, 0) + 1
        cluster_id = max(vote, key=vote.get) if vote else next_id
        if cluster_id in used:            # 旧簇分裂时的兜底，避免 id 冲突
            cluster_id = next_id
        if cluster_id >= next_id:
            next_id = cluster_id + 1
        used.add(cluster_id)

        platforms = {topics[i][0] for i in members}
        if len(platforms) > 1:
            multi += 1

        ref = grams[members[0]]
        for i in members:
            p, nt, title, _ext, _key, malias = topics[i]
            mapping_rows.append(
                (cluster_id, p, nt, title, round(_jaccard(grams[i], ref), 3), malias, ts)
            )

    conn.execute("DELETE FROM topic_cluster")
    conn.executemany(
        "INSERT INTO topic_cluster (cluster_id, platform_code, norm_title, title, similarity, matched_alias, updated_at) "
        "VALUES (?,?,?,?,?,?,?)",
        mapping_rows,
    )
    conn.commit()
    return {"topics": n, "clusters": len(groups), "multi": multi, "alias_hits": alias_hits}


def cluster_signature(conn, *, extra_aliases=None) -> dict[tuple[str, str], int]:
    """反事实评估辅助：返回 {(平台, 归一化标题): 簇代表下标} 的映射。

    用"簇内最小下标"作簇标识（纯计算、与写库后的自增 id 无关），用于比较
    "注入某别名前后"哪些话题的归属发生了变化（impact 计算）。
    """
    res = compute_clusters(conn, extra_aliases=extra_aliases)
    topics, groups = res["topics"], res["groups"]
    sig: dict[tuple[str, str], int] = {}
    for root, members in groups.items():
        rep = min(members)
        for i in members:
            sig[(topics[i][0], topics[i][1])] = rep
    return sig


def alias_impact(conn, alias: str, canonical: str, base_sig=None) -> dict:
    """反事实评估：若把 (alias → canonical) 加入词典，会合并多少话题/平台。

    与真实对齐共用 compute_clusters，口径逐字一致；只是多注入一条别名规则，
    比较前后每个话题的簇归属（簇代表下标）是否变化。

    返回 {"topics": 受影响话题数, "platforms": 受影响平台数}：
      · topics    = 因该别名而"被并入他簇"的话题总数（跨平台聚合增益）；
      · platforms = 这些话题覆盖的平台数。
    两者皆为 0 表示该别名**当前对跨平台聚合无影响**（Q1-B：保留但标低优先级）。
    """
    base = base_sig if base_sig is not None else cluster_signature(conn)
    cf = cluster_signature(conn, extra_aliases=[(alias, canonical)])
    if not base or not cf:
        return {"topics": 0, "platforms": 0}

    changed = {k for k in base if base.get(k) != cf.get(k)}
    if not changed:
        return {"topics": 0, "platforms": 0}

    # 受影响的新簇：由 ≥2 个旧簇合并而来（真正产生了"跨簇聚合"），且含变化话题
    member_cnt: dict[int, int] = {}
    for k in base:
        cf_root = cf.get(k, base[k])
        member_cnt[cf_root] = member_cnt.get(cf_root, 0) + 1
    merged_roots = set()
    for cf_root in member_cnt:
        oldroots = {base[k] for k in base if cf.get(k, base[k]) == cf_root}
        if len(oldroots) >= 2 and any(cf.get(k, base[k]) == cf_root for k in changed):
            merged_roots.add(cf_root)

    topics = sum(member_cnt.get(r, 0) for r in merged_roots)
    platforms = len({k[0] for k in base
                     if cf.get(k, base[k]) in merged_roots})
    return {"topics": topics, "platforms": platforms}
