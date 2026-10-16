"""7 个免费热点平台的适配器：抓取 + 解析 + 归一化。

每个适配器只需实现：
  - fetch() -> (body: bytes, http_status: int)
  - parse(body) -> list[item_dict]

item_dict 统一字段（缺失用 None）：
  position, is_top, title, external_id, url, hot_value, hot_text, label, author, extra
norm_title 由 runner 在入库前统一补全。
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

from .normalize import cn_number

UA_PC = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
         "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
UA_IPHONE = ("Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
             "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/604.1")

TIMEOUT = 20


class AdapterError(RuntimeError):
    pass


class BaseAdapter:
    code = ""
    name = ""
    url = ""
    method = "GET"
    extra_headers: dict[str, str] = {}
    post_data: str | None = None
    ua = UA_PC

    # ---- HTTP ----
    def fetch(self) -> tuple[bytes, int]:
        headers = {"User-Agent": self.ua, "Accept": "application/json, text/plain, */*"}
        headers.update(self.extra_headers)
        req = urllib.request.Request(
            self.url,
            data=self.post_data.encode("utf-8") if self.post_data else None,
            headers=headers,
            method=self.method,
        )
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                return resp.read(), resp.status
        except urllib.error.HTTPError as e:  # 保留 body 以便记录
            raise AdapterError(f"HTTP {e.code}") from e
        except Exception as e:  # noqa: BLE001
            raise AdapterError(f"{type(e).__name__}: {e}") from e

    # ---- 解析 ----
    def _json(self, body: bytes):
        try:
            return json.loads(body)
        except Exception as e:  # noqa: BLE001
            raise AdapterError(f"JSON 解析失败: {e}") from e

    def parse(self, body: bytes) -> list[dict]:
        raise NotImplementedError

    # ---- 工具 ----
    @staticmethod
    def _item(position, title, **kw) -> dict:
        base = dict(position=position, is_top=0, title=title, external_id=None,
                    url=None, hot_value=None, hot_text=None, label=None, author=None, extra=None)
        base.update(kw)
        return base


# ---------------------------------------------------------------- 微博热搜
class WeiboAdapter(BaseAdapter):
    code, name = "weibo", "微博热搜"
    url = "https://weibo.com/ajax/side/hotSearch"
    ua = UA_IPHONE
    extra_headers = {"Referer": "https://weibo.com/"}

    def parse(self, body: bytes) -> list[dict]:
        data = self._json(body).get("data", {})
        items = []
        for i, it in enumerate(data.get("realtime") or [], start=1):
            word = it.get("note") or it.get("word")
            if not word:
                continue
            hot = it.get("num")
            items.append(self._item(
                it.get("realpos") or i,
                word,
                url="https://s.weibo.com/weibo?q=%23" + word + "%23",
                hot_value=hot,
                hot_text=str(hot) if hot is not None else None,
                label=it.get("label_name"),
                extra={"rank": it.get("rank"), "flag": it.get("flag")},
            ))
        # 置顶要闻
        for gov in data.get("hotgovs") or []:
            w = gov.get("name") or gov.get("word")
            if w:
                items.insert(0, self._item(
                    gov.get("pos") or 0, w, is_top=1,
                    url="https://s.weibo.com/weibo?q=" + w,
                    label="要闻", extra={"topic_flag": gov.get("topic_flag")},
                ))
        return items


# ---------------------------------------------------------------- 百度热搜
class BaiduAdapter(BaseAdapter):
    code, name = "baidu", "百度热搜"
    # pc 端接口会返回 hotScore/desc/hotChange；wise 端已不再返回数值热度
    url = "https://top.baidu.com/api/board?platform=pc&tab=realtime"
    ua = UA_PC

    @staticmethod
    def _pick_items(cards: list) -> list:
        """兼容两种结构：pc 为扁平列表；部分接口为 content[0].content 嵌套一层。"""
        for card in cards:
            content = card.get("content")
            if not isinstance(content, list) or not content:
                continue
            first = content[0]
            if isinstance(first, dict) and first.get("word"):
                return content
            if isinstance(first, dict):
                inner = first.get("content")
                if isinstance(inner, list) and inner and isinstance(inner[0], dict):
                    return inner
        return []

    def parse(self, body: bytes) -> list[dict]:
        cards = self._json(body).get("data", {}).get("cards", [])
        raw = self._pick_items(cards)
        items = []
        for i, it in enumerate(raw, start=1):
            word = it.get("word")
            if not word:
                continue
            idx = it.get("index")  # 广告/特殊位返回负数，回落为展示位次
            position = idx if isinstance(idx, int) and idx > 0 else i
            items.append(self._item(
                position,
                word,
                is_top=1 if it.get("isTop") else 0,
                url=it.get("url") or it.get("rawUrl"),
                hot_value=cn_number(it.get("hotScore")),
                hot_text=str(it.get("hotScore")) if it.get("hotScore") is not None else None,
                label=it.get("newHotName") or it.get("hotTag") or ("置顶" if it.get("isTop") else None),
                extra={"desc": it.get("desc"), "hot_change": it.get("hotChange")},
            ))
        return items


# ---------------------------------------------------------------- 知乎热榜
class ZhihuAdapter(BaseAdapter):
    code, name = "zhihu", "知乎热榜"
    url = "https://api.zhihu.com/topstory/hot-list?limit=50"

    def parse(self, body: bytes) -> list[dict]:
        data = self._json(body).get("data", [])
        items = []
        for i, entry in enumerate(data, start=1):
            target = entry.get("target") or {}
            title = target.get("title")
            if not title:
                continue
            qid = target.get("id")
            detail = entry.get("detail_text")  # 例如 "708 万热度"
            items.append(self._item(
                i,
                title,
                external_id=str(qid) if qid else None,
                url=f"https://www.zhihu.com/question/{qid}" if qid else None,
                hot_value=cn_number(detail),
                hot_text=detail,
                label="新" if entry.get("debut") else None,
                author=(target.get("author") or {}).get("name"),
                extra={"answer_count": target.get("answer_count"),
                       "follower_count": target.get("follower_count"),
                       "excerpt": target.get("excerpt")},
            ))
        return items


# ---------------------------------------------------------------- 头条热榜
class ToutiaoAdapter(BaseAdapter):
    code, name = "toutiao", "头条热榜"
    url = "https://www.toutiao.com/hot-event/hot-board/?origin=toutiao_pc"

    def parse(self, body: bytes) -> list[dict]:
        data = self._json(body).get("data", [])
        items = []
        for i, it in enumerate(data, start=1):
            title = it.get("Title")
            if not title:
                continue
            items.append(self._item(
                i,
                title,
                external_id=it.get("ClusterIdStr") or (str(it.get("ClusterId")) if it.get("ClusterId") else None),
                url=it.get("Url"),
                hot_value=cn_number(it.get("HotValue")),
                label=it.get("Label") or None,
                extra={"query_word": it.get("QueryWord"), "image": it.get("Image")},
            ))
        return items


# ---------------------------------------------------------------- B站热搜
class BilibiliAdapter(BaseAdapter):
    code, name = "bilibili", "B站热搜"
    url = "https://api.bilibili.com/x/web-interface/search/square?limit=50"

    def parse(self, body: bytes) -> list[dict]:
        trending = (self._json(body).get("data") or {}).get("trending") or {}
        items = []
        for i, it in enumerate(trending.get("list") or [], start=1):
            title = it.get("show_name") or it.get("keyword")
            if not title:
                continue
            items.append(self._item(
                i,
                title,
                url="https://search.bilibili.com/all?keyword=" + urllib.parse.quote(title),
                hot_value=cn_number(it.get("heat_score")),
                hot_text=str(it.get("heat_score")) if it.get("heat_score") is not None else None,
                extra={"icon": it.get("icon")},
            ))
        return items


# ---------------------------------------------------------------- 36氪热榜
class Kr36Adapter(BaseAdapter):
    code, name = "36kr", "36氪热榜"
    url = "https://gateway.36kr.com/api/mis/nav/home/nav/rank/hot"
    method = "POST"
    post_data = '{"partner_id":"web","param":{"siteId":1,"platformId":2}}'
    extra_headers = {"Content-Type": "application/json", "Referer": "https://36kr.com/"}

    def parse(self, body: bytes) -> list[dict]:
        data = self._json(body).get("data", {})
        items = []
        for i, it in enumerate(data.get("hotRankList") or [], start=1):
            mat = it.get("templateMaterial") or {}
            title = mat.get("widgetTitle")
            if not title:
                continue
            item_id = it.get("itemId")
            items.append(self._item(
                i,
                title,
                external_id=str(item_id) if item_id else None,
                url=("https://36kr.com/" + it["route"]) if it.get("route") else None,
                hot_value=cn_number(mat.get("statRead")),
                hot_text=mat.get("statFormat"),
                author=mat.get("authorName"),
                extra={"read": mat.get("statRead"), "praise": mat.get("statPraise"),
                       "collect": mat.get("statCollect"), "comment": mat.get("statComment")},
            ))
        return items


# ---------------------------------------------------------------- 华尔街见闻
class WallStreetCnAdapter(BaseAdapter):
    code, name = "wallstreetcn", "华尔街见闻"
    url = "https://api-one.wallstcn.com/apiv1/content/lives?channel=global-channel&limit=30"

    def parse(self, body: bytes) -> list[dict]:
        data = self._json(body).get("data", {})
        items = []
        for i, it in enumerate(data.get("items") or [], start=1):
            title = it.get("title") or it.get("content_text")
            if not title:
                continue
            title = title.strip()
            items.append(self._item(
                i,
                title[:200],
                external_id=str(it.get("id")) if it.get("id") else None,
                url=it.get("uri"),
                hot_value=cn_number(it.get("score")),
                hot_text=str(it.get("score")) if it.get("score") is not None else None,
                author=(it.get("author") or {}).get("display_name"),
                extra={"display_time": it.get("display_time"),
                       "comment_count": it.get("comment_count"),
                       "channels": it.get("channels")},
            ))
        return items


ADAPTERS: dict[str, BaseAdapter] = {
    a.code: a for a in [
        WeiboAdapter(), BaiduAdapter(), ZhihuAdapter(), ToutiaoAdapter(),
        BilibiliAdapter(), Kr36Adapter(), WallStreetCnAdapter(),
    ]
}

# 各平台"热度原始值"的口径说明（写入 word_metric.metric_type，用于跨平台可比时标注）
METRIC_TYPES: dict[str, str] = {
    "weibo":        "讨论热度",   # note/word 的 num（微博讨论热度）
    "baidu":        "搜索热度",   # hotScore（百度搜索指数）
    "zhihu":        "热度值",     # detail_text "xxx 万热度"
    "toutiao":      "热度值",     # HotValue
    "bilibili":     "热度值",     # heat_score
    "36kr":         "阅读量",     # statRead
    "wallstreetcn": "重要度",     # score
}


def metric_type_of(code: str) -> str:
    """返回平台的原始热度口径；未知平台回落为"热度值"。"""
    return METRIC_TYPES.get(code, "热度值")


def get(code: str) -> BaseAdapter:
    if code not in ADAPTERS:
        raise AdapterError(f"未知平台适配器: {code}")
    return ADAPTERS[code]
