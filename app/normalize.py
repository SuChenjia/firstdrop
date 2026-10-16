"""标题归一化与数值解析工具。

用于把不同平台的榜单标题映射到统一的可比较键，从而支撑
"同一话题跨批次追踪（首次出现/上榜时长/峰值名次）"。
"""
from __future__ import annotations

import re
import unicodedata

_WS = re.compile(r"\s+")
_PUNCT = re.compile(r"[\-—–_·・…\.\,\!\?\:;、。，！？；：\"'“”‘’()（）\[\]【】《》「」<>→\+]+")
_NUM = re.compile(r"(\d+(?:\.\d+)?)")


def normalize_title(text: str | None) -> str:
    """全角转半角 → 去除话题标记/标点/空白 → 小写。

    保留中文与字母数字，尽量让同一话题在不同批次得到同一个键。
    """
    if not text:
        return ""
    s = unicodedata.normalize("NFKC", str(text))
    s = s.replace("#", "")
    s = _PUNCT.sub("", s)
    s = _WS.sub("", s)
    return s.strip().lower()


def cn_number(text: str | None) -> int | None:
    """从中文热度文本中解析数值，例如 "708 万热度" -> 7080000，"3.2亿" -> 320000000。

    也兼容直接传入 int/float（此时原样取整）。
    """
    if text is None:
        return None
    s = str(text)
    m = _NUM.search(s)
    if not m:
        return None
    value = float(m.group(1))
    if "亿" in s:
        value *= 100_000_000
    elif "万" in s:
        value *= 10_000
    return int(value)


def to_int(value) -> int | None:
    """把 '33647045' / 33647045 / '3.4万' 之类的输入统一为 int。"""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    return cn_number(str(value))
