"""事件首发溯源：对"值得追"的触发事件，一次性检索"已覆盖源中最早的发声点"。

边界（与设计稿一致）：
  · 不声称"全网首发"，只输出"已覆盖源中最早"，并附覆盖源清单与置信度；
  · 只对触发事件做，不做全量帖子级采集；
  · 溯源靠公开可访问的搜索源 + 通用正文抽取，纯标准库实现（零第三方依赖）。

流程：触发判定 register_triggers() → 关键词扩展 → 多源搜索 search_news()
     → 候选去重/相关度预筛 → 通用正文抽取 extract_article()
     → 最早信号判定 → 覆盖度与置信度 → 落库 event_earliest_signal。

现状覆盖源（实测可达）：搜狗微信（公众号）、360 新闻、360 网页。
百度新闻/必应/Google 当前被反爬或纯 JS 渲染，未纳入；新增源只需往 SOURCES 里加一个函数。
"""
from __future__ import annotations

import html
import json
import re
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from html.parser import HTMLParser
from pathlib import Path

from .db import now_iso

# ---- 调参区 -------------------------------------------------------------
SEARCH_TIMEOUT = 20        # 单次 HTTP 超时（秒）
MAX_CANDIDATES = 30        # 每事件进入排序的候选上限
FETCH_TOP_K = 5            # 实际抓正文的候选数
DAILY_SEARCH_QUOTA = 60    # 每日溯源事件数上限（预算控制的兜底默认值；实际取配置，见 daily_quota()）
MAX_NEW_TRIGGERS = 40      # 单次触发注册新增上限（防止快照翻新时刷屏）
MAX_QUERIES = 2            # 每事件最多发出的检索词数
RELEVANCE_MIN = 0.35       # 相关度阈值（查询标题 bigram 被候选覆盖的比例）
LOOKBACK_DAYS = 90         # 首发信号时间下界：只认"事件触发前 90 天以内"的发声点
                           # （热榜事件几乎都是新兴事件；过老的多为"同话题不同事件"的旧文，
                           #   这是压制误报最有效的一道护栏）

# 快照保存目录
SNAPSHOT_DIR = Path(__file__).resolve().parent.parent / "data" / "snapshots"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

# 权威/官方站点：命中则提权（仅影响置信度，不影响时间排序）
AUTHORITATIVE = (
    "gov.cn", "xinhuanet", "news.cn", "people.com.cn", "cctv", "chinanews",
    "thepaper", "china.com.cn", "ce.cn", "yicai", "caixin", "chinadaily",
    "bjnews", "jfdaily", "stcn", "21jingji", "nbd.com.cn", "thecover",
)


# =============================== 通用小工具 ===============================
def _now():
    return datetime.now().astimezone()


def _fetch(url, referer=None, timeout=SEARCH_TIMEOUT) -> bytes:
    headers = {"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9"}
    if referer:
        headers["Referer"] = referer
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _decode(body: bytes) -> str:
    head = body[:2048].decode("ascii", "ignore")
    m = re.search(r'charset=["\']?([\w-]+)', head, re.I)
    enc = m.group(1) if m else "utf-8"
    if enc.lower() in ("gb2312", "gbk", "gb18030"):
        enc = "gb18030"
    try:
        return body.decode(enc, "replace")
    except LookupError:
        return body.decode("utf-8", "replace")


def _strip(s: str) -> str:
    """去标签 + 反转义 + 压空白。"""
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", s or ""))).strip()


def save_snapshot(url: str, cluster_id: int) -> str:
    """抓取 URL 原始 HTML 并保存到本地，返回相对路径（供看板访问）。失败返回空串。"""
    try:
        body = _fetch(url)
    except Exception:
        return ""
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    fname = f"{cluster_id}_{_now().strftime('%Y%m%d_%H%M%S')}.html"
    path = SNAPSHOT_DIR / fname
    try:
        path.write_bytes(body)
    except OSError:
        return ""
    return f"snapshots/{fname}"


def _host(url: str) -> str:
    try:
        return urllib.parse.urlsplit(url).netloc.lower().lstrip("www.")
    except Exception:
        return ""


def _norm_url(url: str) -> str:
    """去 fragment / 常见追踪参数，用于候选去重。"""
    try:
        u = urllib.parse.urlsplit(url)
        q = urllib.parse.parse_qsl(u.query)
        q = [(k, v) for k, v in q if not k.lower().startswith(("utm_", "spm", "from", "src"))
             and k.lower() not in ("ref", "share_token")]
        return urllib.parse.urlunsplit((u.scheme, u.netloc, u.path, urllib.parse.urlencode(q), ""))
    except Exception:
        return url


# ---- 时间解析（搜索摘要 / 正文 meta 里的时间统一成 ISO 本地时间串） ----
# 支持：2026-10-15 07:34:38 / 2026-10-15T07:34:38+08:00 / 2026年10月15日，13:04
_DT = re.compile(r"(20\d{2})[-/年](\d{1,2})[-/月](\d{1,2})[日]?\s*[T\s,，]*\s*(\d{1,2})[:时：](\d{2})(?:[:：分](\d{2}))?")
_D = re.compile(r"(20\d{2})[-/年](\d{1,2})[-/月](\d{1,2})")
_REL = re.compile(r"(\d+)\s*(分钟|小时|天)前")


def _iso(y, m, d, hh=0, mm=0, ss=0) -> str:
    try:
        return datetime(int(y), int(m), int(d), int(hh or 0), int(mm or 0), int(ss or 0),
                        tzinfo=_now().tzinfo).isoformat(timespec="seconds")
    except ValueError:
        return ""


def parse_time(text: str, ref=None) -> str:
    """从任意文本里抽发布时间；支持绝对时间、相对时间（N分钟前）。"""
    if not text:
        return ""
    ref = ref or _now()
    m = _DT.search(text)
    if m:
        return _iso(*m.groups())
    m = _D.search(text)
    if m:
        return _iso(*m.groups())
    m = _REL.search(text)
    if m:
        n, unit = int(m.group(1)), m.group(2)
        delta = {"分钟": timedelta(minutes=n), "小时": timedelta(hours=n),
                 "天": timedelta(days=n)}[unit]
        return (ref - delta).isoformat(timespec="seconds")
    if "昨天" in text:
        return (ref - timedelta(days=1)).replace(hour=0, minute=0, second=0).isoformat(timespec="seconds")
    return ""


def parse_datetime(text: str) -> str:
    """只匹配带时分的时间（YYYY-MM-DD HH:MM[:SS]），不匹配纯日期。"""
    if not text:
        return ""
    m = _DT.search(text)
    return _iso(*m.groups()) if m else ""


# =============================== 搜索源 ===============================
def _src_sogou_weixin(query: str, limit: int = 10) -> list[dict]:
    """搜狗微信：公众号文章。返回 [{url,title,summary,site,posted_at}]。"""
    url = "https://weixin.sogou.com/weixin?type=2&query=" + urllib.parse.quote(query)
    text = _decode(_fetch(url, referer="https://weixin.sogou.com/"))
    out = []
    blocks = re.finditer(r'<div class="txt-box">(.*?)(?=<div class="txt-box">|</ul>)', text, re.S)
    for b in blocks:
        seg = b.group(1)
        a = re.search(r'href="(/link\?url=[^"]+)"', seg)
        if not a:
            continue
        href = html.unescape(a.group(1))
        t = re.search(r"<h3>\s*<a[^>]*>(.*?)</a>", seg, re.S)
        p = re.search(r'<p class="txt-info"[^>]*>(.*?)</p>', seg, re.S)
        s = re.search(r'<span class="all-time-y2">(.*?)</span>', seg, re.S)
        e = re.search(r"timeConvert\('(\d+)'\)", seg)
        posted = ""
        if e:
            posted = datetime.fromtimestamp(int(e.group(1)), _now().tzinfo).isoformat(timespec="seconds")
        out.append({
            "url": urllib.parse.urljoin("https://weixin.sogou.com/", href),
            "title": _strip(t.group(1)) if t else "",
            "summary": _strip(p.group(1)) if p else "",
            "site": _strip(s.group(1)) if s else "微信公众号",
            "posted_at": posted,
        })
        if len(out) >= limit:
            break
    return out


def _src_so_news(query: str, limit: int = 15) -> list[dict]:
    """360 新闻：li.res-list[data-url] + h3 标题 + p.summary（含绝对时间）+ cite 站点。"""
    url = "https://news.so.com/ns?q=" + urllib.parse.quote(query) + "&tn=news"
    text = _decode(_fetch(url))
    out = []
    for m in re.finditer(r'<li class="[^"]*res-list[^"]*"[^>]*data-url="([^"]+)"(.*?)</li>', text, re.S):
        real, seg = html.unescape(m.group(1)), m.group(2)
        t = re.search(r"<h3[^>]*>(.*?)</h3>", seg, re.S)
        p = re.search(r'<p class="summary[^"]*">(.*?)</p>', seg, re.S)
        s = re.search(r"<cite[^>]*>(.*?)</cite>", seg, re.S)
        rel = re.search(r'class="[^"]*\btime\b[^"]*"[^>]*>(.*?)</span>', seg, re.S)
        summary = _strip(p.group(1)) if p else ""
        out.append({
            "url": real,
            "title": _strip(t.group(1)) if t else "",
            "summary": summary,
            # summary 形如 "2026-10-15 13:46 懂球帝 正文…"，优先取其中的绝对时间
            "posted_at": parse_time(summary) or parse_time(_strip(rel.group(1)) if rel else ""),
            "site": _strip(s.group(1)) if s else _host(real),
        })
        if len(out) >= limit:
            break
    return out


def _src_so_web(query: str, limit: int = 15) -> list[dict]:
    """360 网页：新闻卡片 a[data-mdurl].mh-news-title + 邻近 p.mh-news-desc（含时间）。"""
    url = "https://www.so.com/s?q=" + urllib.parse.quote(query)
    text = _decode(_fetch(url))
    out = []
    for m in re.finditer(r'<a[^>]*data-mdurl="([^"]+)"[^>]*class="mh-news-title[^"]*"[^>]*>(.*?)</a>', text, re.S):
        real, title = html.unescape(m.group(1)), _strip(m.group(2))
        window = text[m.end():m.end() + 700]
        d = re.search(r'<p class="mh-news-desc[^"]*">(.*?)</p>', window, re.S)
        src = re.search(r'<span class="mh-src[^"]*">(.*?)</span>', window, re.S)
        pdt = re.search(r'<span class="mh-pdate[^"]*">(.*?)</span>', window, re.S)
        summary = _strip(d.group(1)) if d else ""
        out.append({
            "url": real,
            "title": title,
            "summary": summary,
            "posted_at": parse_time(summary) or parse_time(_strip(pdt.group(1)) if pdt else ""),
            "site": _strip(src.group(1)) if src else _host(real),
        })
        if len(out) >= limit:
            break
    return out


SOURCES = (
    ("sogou_weixin", _src_sogou_weixin),
    ("so_news", _src_so_news),
    ("so_web", _src_so_web),
)


def search_news(queries: list[str]) -> tuple[list[dict], list[str]]:
    """多源多词检索并去重。返回 (候选列表, 成功命中的源清单)。"""
    cands: dict[str, dict] = {}
    coverage: list[str] = []
    for name, fn in SOURCES:
        ok = False
        for q in queries:
            try:
                for c in fn(q):
                    if not c.get("url") or not c.get("title"):
                        continue
                    ok = True
                    key = _norm_url(c["url"])
                    c["source"] = name
                    cands.setdefault(key, c)
            except Exception:
                continue
        if ok:
            coverage.append(name)
    return list(cands.values()), coverage


# =============================== 通用正文抽取 ===============================
class _ArticleParser(HTMLParser):
    """轻量正文抽取：meta 时间/标题 + 段落聚合（纯标准库，close 到 readability 思路）。"""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.metas: dict[str, str] = {}
        self.title = ""
        self.times: list[str] = []
        self.paras: list[str] = []
        self._skip = 0
        self._in_title = False
        self._in_p = False
        self._tbuf: list[str] = []
        self._pbuf: list[str] = []

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag in ("script", "style", "noscript"):
            self._skip += 1
        elif tag == "meta":
            key = (a.get("property") or a.get("name") or "").lower()
            if key and a.get("content"):
                self.metas[key] = a["content"]
        elif tag == "time":
            if a.get("datetime"):
                self.times.append(a["datetime"])
        elif tag == "title":
            self._in_title = True
            self._tbuf = []
        elif tag == "p" and not self._skip:
            self._in_p = True
            self._pbuf = []

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript") and self._skip:
            self._skip -= 1
        elif tag == "title" and self._in_title:
            self._in_title = False
            if not self.title:
                self.title = "".join(self._tbuf).strip()
        elif tag == "p" and self._in_p:
            self._in_p = False
            t = _strip("".join(self._pbuf))
            if len(t) >= 15:
                self.paras.append(t)

    def handle_data(self, data):
        if self._skip or not data:
            return
        if self._in_title:
            self._tbuf.append(data)
        elif self._in_p:
            self._pbuf.append(data)


def _is_precise(iso_str: str) -> bool:
    """ISO 时间是否精确到时分（非 00:00:00）。"""
    try:
        dt = datetime.fromisoformat(iso_str)
        return dt.hour != 0 or dt.minute != 0 or dt.second != 0
    except ValueError:
        return False


def extract_article(url: str) -> dict | None:
    """抓取并抽取单篇文章的标题 / 正文 / 发布时间 / 站点。失败返回 None。"""
    try:
        body = _fetch(url)
    except Exception:
        return None
    text = _decode(body)
    p = _ArticleParser()
    try:
        p.feed(text)
    except Exception:
        pass

    title = ""
    for k in ("og:title", "twitter:title"):
        if p.metas.get(k):
            title = p.metas[k]
            break
    title = _strip(title) or p.title

    # 收集所有候选时间（meta / JSON-LD / <time>），优先选带时分的
    cands: list[str] = []
    for k in ("article:published_time", "og:published_time", "datepublished",
              "pubdate", "publishdate", "weibo:article:create_at", "og:release_date"):
        if p.metas.get(k):
            t = parse_time(p.metas[k])
            if t:
                cands.append(t)
    m = re.search(r'"datePublished"\s*:\s*"([^"]+)"', text)
    if m:
        t = parse_time(m.group(1))
        if t:
            cands.append(t)
    if p.times:
        t = parse_time(p.times[0])
        if t:
            cands.append(t)

    posted = ""
    for t in cands:
        if _is_precise(t):
            posted = t
            break
    # 注意：这里不急着用纯日期候选，先让 content/text 兜底有机会搜到更精确的时间

    content = "\n".join(p.paras)
    if not posted:
        # 兜底1：正文段落里找带时分的时间（避免被 head/script 稀释）
        posted = parse_datetime(content)
    if not posted:
        # 兜底2：原始 HTML 里找带时分的时间（有些站把发布时间放非 <p> 标签里）
        posted = parse_datetime(text)
    if not posted:
        # 兜底3：正文里只有日期
        posted = parse_time(content)
    if not posted and cands:
        # 最终兜底：候选里的纯日期
        posted = cands[0]
    if len(content) < 40:
        return None
    return {"url": url, "title": title, "content": content,
            "posted_at": posted, "site": _host(url)}


# =============================== 相关度 / 置信度 ===============================
# 通用字（虚词/高频套话）：出现在 bigram 里会让"同话题不同事件"的旧文意外高重合
_STOP = frozenset(
    "的了是在就已未还又再更最很都也而被把让向从对于与和及或各该此其之这那就"
    "什么为么样怎吗呢否认同称说正式开通发现回应表示认为媒评通报曝去涨跌情况"
    "月日年时分秒中上下一多少万亿个条次项名位家人员事消息报道记者最新独家"
)


def _bigrams(s: str) -> set[str]:
    """从文本抽"有信息量"的 bigram（含 CJK 二元组）+ ASCII 词 tonen。"""
    out: set[str] = set()
    for run in re.findall(r"[\u4e00-\u9fff]{2,}", s or ""):
        for i in range(len(run) - 1):
            bg = run[i:i + 2]
            if bg[0] in _STOP or bg[1] in _STOP:
                continue
            out.add(bg)
    out |= {w.lower() for w in re.findall(r"[A-Za-z0-9]{2,}", s or "")}
    return out


def _relevance(query_title: str, text: str) -> float:
    """查询标题的 bigram 被候选文本覆盖的比例（对词序不敏感，抗长文稀释）。"""
    q = _bigrams(query_title)
    if not q:
        return 0.0
    return len(q & _bigrams(text)) / len(q)


def _coverage_score(n_sources: int) -> float:
    return min(1.0, n_sources / 3.0)


def _time_score(posted_at: str, from_article: bool) -> float:
    if not posted_at:
        return 0.35
    return 1.0 if from_article else 0.7


def _authority(site: str) -> float:
    s = (site or "").lower()
    return 1.0 if any(a in s for a in AUTHORITATIVE) else 0.0


def _confidence(n_sources, posted_at, from_article, rel, site) -> float:
    v = (0.35 * _coverage_score(n_sources) + 0.25 * _time_score(posted_at, from_article)
         + 0.25 * min(1.0, rel) + 0.15 * _authority(site))
    return round(min(1.0, max(0.0, v)), 2)


# =============================== 触发判定 ===============================
def _plans_coverage() -> str:
    return json.dumps([n for n, _ in SOURCES], ensure_ascii=False)


def _has_cluster_alert(conn, cluster_id: int) -> bool:
    return conn.execute(
        "SELECT 1 FROM alert WHERE scope='cluster' AND cluster_id=? LIMIT 1",
        (cluster_id,)).fetchone() is not None


def _decide(conn, row, seen_runs: int):
    """返回 (触发原因, 层级) 或 (None, None)。"""
    pc = row["platform_count"] or 0
    bp = row["best_position"]
    if pc >= 2:
        return "cross_platform", "L1"
    if bp is not None and bp <= 3:
        return "top3", "L1"
    if _has_cluster_alert(conn, row["cluster_id"]):
        return "rank_breakout", "L1"
    if bp is not None and bp <= 10 and seen_runs >= 3:
        return "top10_persist", "L2"
    return None, None


def register_triggers(conn) -> dict:
    """扫描当前簇快照，把命中的事件注册进 event_track（同 cluster 只注册一次）。"""
    rows = [dict(r) for r in conn.execute(
        "SELECT cluster_id, platform_count, platforms, peak_hot, best_position, label "
        "FROM v_cluster")]
    seen = {r["cluster_id"]: r["n"] for r in conn.execute(
        "SELECT tc.cluster_id, MAX(tl.seen_runs) AS n FROM topic_cluster tc "
        "JOIN v_topic_lifecycle tl "
        "  ON tl.platform_code=tc.platform_code AND tl.norm_title=tc.norm_title "
        "GROUP BY tc.cluster_id")}
    alive = {r[0] for r in conn.execute(
        "SELECT cluster_id FROM event_track WHERE status IN ('pending','active','paused')")}

    picked = []
    for r in rows:
        if r["cluster_id"] in alive:
            continue
        if not r["label"]:
            continue  # 空标题（v_cluster.label 为 NULL 时 topic_cluster 成员在 v_latest_item 中无匹配）
        reason, tier = _decide(conn, r, seen.get(r["cluster_id"], 0))
        if reason:
            picked.append((tier, reason, r))
    # 优先级：L1 优先，其次覆盖平台数、峰值热度
    picked.sort(key=lambda x: (0 if x[0] == "L1" else 1,
                               -(x[2]["platform_count"] or 0), -(x[2]["peak_hot"] or 0)))
    picked = picked[:MAX_NEW_TRIGGERS]

    ts = now_iso()
    for tier, reason, r in picked:
        conn.execute(
            "INSERT OR IGNORE INTO event_track (cluster_id, title, tier, status, trigger_reason, "
            "platform_count, platforms, peak_hot, best_position, coverage, triggered_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (r["cluster_id"], r["label"], tier, "pending", reason,
             r["platform_count"], r["platforms"], r["peak_hot"], r["best_position"],
             _plans_coverage(), ts, ts))
    if picked:
        conn.commit()
    return {"scanned": len(rows), "new": len(picked),
            "l1": sum(1 for p in picked if p[0] == "L1")}


# =============================== 溯源执行 ===============================
def _query_terms(conn, cluster_id: int, title: str) -> list[str]:
    """检索词：代表标题 + 簇内最多 1 条不同措辞的成员标题（提升召回）。"""
    qs = [title] if title else []
    for r in conn.execute(
        "SELECT DISTINCT title FROM topic_cluster WHERE cluster_id=? LIMIT 6", (cluster_id,)):
        t = r["title"]
        if t and t not in qs:
            qs.append(t)
        if len(qs) >= MAX_QUERIES:
            break
    return qs[:MAX_QUERIES]


def trace_one(conn, cluster_id: int, title: str, triggered_at: str = "") -> dict | None:
    """对单个事件执行首发溯源，命中则写 event_earliest_signal。"""
    if conn.execute("SELECT 1 FROM event_earliest_signal WHERE cluster_id=? AND is_manual=1",
                    (cluster_id,)).fetchone():
        return None  # 人工补链优先，不覆盖

    terms = _query_terms(conn, cluster_id, title)
    if not terms:
        return None
    cands, coverage = search_news(terms)
    if not cands:
        return None

    # 时间下界：热榜事件多是新兴事件，早于触发前 LOOKBACK_DAYS 的候选直接判为"同话题不同事件"
    ref = _now()
    try:
        base = datetime.fromisoformat(triggered_at) if triggered_at else ref
    except ValueError:
        base = ref
    floor = (base - timedelta(days=LOOKBACK_DAYS)).isoformat(timespec="seconds")

    main = terms[0]
    scored = []
    for c in cands:
        rel = _relevance(main, c.get("title", "") + " " + c.get("summary", ""))
        if rel < RELEVANCE_MIN:
            continue
        posted = c.get("posted_at") or ""
        if posted and posted < floor:
            continue  # 过旧，剔除
        scored.append((c, rel))
    if not scored:
        return None
    # 先按搜索摘要给出的时间升序，取最早的若干条去抓正文（正文时间更可靠）
    scored.sort(key=lambda x: (x[0].get("posted_at") or "9999", -x[1]))
    scored = scored[:FETCH_TOP_K * 2][:MAX_CANDIDATES]

    best = None
    for c, rel in scored[:FETCH_TOP_K]:
        art = extract_article(c["url"])
        if art:
            from_article = bool(art["posted_at"])
            posted = art["posted_at"] or c.get("posted_at") or ""
            if posted and posted < floor:
                continue
            rel2 = _relevance(main, art.get("title", "") + " " + art.get("content", ""))
            cand = {
                "url": c["url"], "site": c.get("site") or art.get("site"),
                "posted_at": posted, "title": art.get("title") or c.get("title"),
                "excerpt": (art.get("content") or "").strip().replace("\n", " ")[:300],
                "from_article": from_article, "rel": max(rel, rel2),
            }
        else:
            cand = {
                "url": c["url"], "site": c.get("site"), "posted_at": c.get("posted_at") or "",
                "title": c.get("title"), "excerpt": (c.get("summary") or "")[:300],
                "from_article": False, "rel": rel,
            }
        if not best or (cand["posted_at"] or "9999") < (best["posted_at"] or "9999"):
            best = cand
    if not best:
        return None

    conf = _confidence(len(coverage), best["posted_at"], best["from_article"],
                       best["rel"], best["site"])
    snapshot_path = save_snapshot(best["url"], cluster_id)
    conn.execute(
        "INSERT OR REPLACE INTO event_earliest_signal (cluster_id, url, site, posted_at, title, "
        "content_excerpt, source_coverage, confidence, is_manual, searched_at, snapshot_path) "
        "VALUES (?,?,?,?,?,?,?,?,0,?,?)",
        (cluster_id, best["url"], best["site"], best["posted_at"], best["title"],
         best["excerpt"], json.dumps(coverage, ensure_ascii=False), conf, now_iso(), snapshot_path))
    return {"cluster_id": cluster_id, "url": best["url"], "site": best["site"],
            "posted_at": best["posted_at"], "confidence": conf, "coverage": coverage,
            "snapshot_path": snapshot_path}


def daily_quota() -> int:
    """当日溯源预算：取 config/sources.toml 的 [defaults].trace_daily_quota（看板可改）。

    配置文件缺失/损坏时回落到常量 DAILY_SEARCH_QUOTA，保证采集不因配置问题中断。
    """
    from . import config as src_config
    try:
        return int(src_config.load().trace_daily_quota)
    except Exception:  # noqa: BLE001
        return DAILY_SEARCH_QUOTA


def run(conn, limit: int = 5, quota: int | None = None) -> dict:
    """对待溯源事件执行溯源（带每日预算控制）。quota 缺省时从配置读取。"""
    if quota is None:
        quota = daily_quota()
    today = _now().strftime("%Y-%m-%d")
    used = conn.execute(
        "SELECT COUNT(*) AS c FROM event_track "
        "WHERE last_traced_at IS NOT NULL AND substr(last_traced_at,1,10)=?",
        (today,)).fetchone()["c"]
    room = max(0, quota - used)
    n = min(limit, room)
    stats = {"traced": 0, "signals": 0, "skipped_quota": room == 0, "quota_used": used,
             "quota": quota, "candidates": 0}
    if n <= 0:
        return stats

    rows = [dict(r) for r in conn.execute(
        "SELECT cluster_id, title, triggered_at FROM event_track WHERE status='pending' "
        "ORDER BY CASE tier WHEN 'L1' THEN 0 ELSE 1 END, triggered_at DESC LIMIT ?", (n,))]
    for r in rows:
        cid = r["cluster_id"]
        res = trace_one(conn, cid, r["title"] or "", r["triggered_at"] or "")
        stats["traced"] += 1
        if res:
            stats["signals"] += 1
        conn.execute(
            "UPDATE event_track SET status='active', trace_count=trace_count+1, "
            "last_traced_at=?, updated_at=? WHERE cluster_id=?",
            (now_iso(), now_iso(), cid))
        # 每事件提交：溯源单个事件要联网检索数十秒，事务不能跨事件持有，
        # 否则长事务会锁住 SQLite，导致调度器/看板的写入报 database is locked
        conn.commit()
    return stats


def set_status(conn, cluster_id: int, status: str) -> bool:
    if status not in ("pending", "active", "paused", "ended"):
        return False
    cur = conn.execute("UPDATE event_track SET status=?, updated_at=? WHERE cluster_id=?",
                       (status, now_iso(), cluster_id))
    conn.commit()
    return cur.rowcount > 0


def set_manual_signal(conn, cluster_id: int, url: str) -> bool:
    """人工补链：直接写入首发信号（is_manual=1），并置为 active。"""
    if not url:
        return False
    ts = now_iso()
    conn.execute(
        "INSERT OR REPLACE INTO event_earliest_signal (cluster_id, url, site, posted_at, title, "
        "content_excerpt, source_coverage, confidence, is_manual, searched_at) "
        "VALUES (?,?,?,?,?,?,'[\"manual\"]',1.0,1,?)",
        (cluster_id, url, _host(url), "", "(人工指定首发链接)", "", ts))
    conn.execute("UPDATE event_track SET status='active', updated_at=? WHERE cluster_id=?",
                 (ts, cluster_id))
    conn.commit()
    return True
