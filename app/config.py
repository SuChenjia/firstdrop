"""数据源配置：从 config/sources.toml 读取开关与调度参数，并同步进 platform 表。

设计要点（代码与数据源解耦）：
  · 平台清单、启用开关、抓取间隔、重试策略全部来自配置文件，代码里不再硬编码。
  · 调度器只消费这里解析出的 SourceSpec；platform 表仅作只读镜像，供看板/报表展示。
  · 配置文件里被删除/注释掉的平台，同步时会被置为 enabled=0，即"不配置=不抓"。
  · 看板「数据源配置」页可通过 build()/save() 反向写回本文件（适配器须已实现）。
"""
from __future__ import annotations

import json
import tomllib
from dataclasses import dataclass
from pathlib import Path

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "sources.toml"

# 未显式配置时的回落默认值
DEFAULTS: dict = {
    "enabled": True,
    "interval_sec": 300,
    "max_retries": 2,
    "retry_delay_sec": 5,
    "enrich_interval_sec": 600,
    "trace_daily_quota": 60,   # 每日溯源事件数上限（预算控制）
}


@dataclass
class Source:
    """单个数据源的调度规格。"""

    code: str
    name: str
    adapter: str
    enabled: bool
    interval_sec: int
    max_retries: int
    retry_delay_sec: int
    sort_order: int


@dataclass
class Config:
    sources: list[Source]
    enrich_interval_sec: int
    path: Path
    trace_daily_quota: int = DEFAULTS["trace_daily_quota"]

    def enabled(self) -> list[Source]:
        return [s for s in self.sources if s.enabled]


def load(path: str | Path = CONFIG_PATH) -> Config:
    """读取 TOML 配置并合并默认值。文件不存在时抛出 FileNotFoundError（由调用方决定回落策略）。"""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"数据源配置不存在: {p}")
    raw = tomllib.loads(p.read_text(encoding="utf-8"))
    defaults = {**DEFAULTS, **(raw.get("defaults") or {})}

    sources: list[Source] = []
    seen: set[str] = set()
    for i, item in enumerate(raw.get("source") or []):
        code = str(item.get("code") or "").strip()
        if not code:
            raise ValueError(f"[[source]] 第 {i + 1} 项缺少 code")
        if code in seen:
            raise ValueError(f"重复的 code: {code}")
        seen.add(code)
        sources.append(Source(
            code=code,
            name=str(item.get("name") or code),
            adapter=str(item.get("adapter") or code),
            enabled=bool(item.get("enabled", defaults["enabled"])),
            interval_sec=int(item.get("interval_sec", defaults["interval_sec"])),
            max_retries=int(item.get("max_retries", defaults["max_retries"])),
            retry_delay_sec=int(item.get("retry_delay_sec", defaults["retry_delay_sec"])),
            sort_order=int(item.get("sort_order", i + 1)),
        ))
    return Config(sources=sources,
                  enrich_interval_sec=int(defaults["enrich_interval_sec"]),
                  path=p,
                  trace_daily_quota=int(defaults.get("trace_daily_quota", DEFAULTS["trace_daily_quota"])))


def sync_platforms(conn, cfg: Config) -> dict:
    """把配置镜像到 platform 表，并停用配置中不存在的平台（实现"注释掉即停爬"）。

    返回统计：{'configured','enabled','disabled_removed'}。
    """
    for s in cfg.sources:
        conn.execute(
            """INSERT INTO platform
                 (code, name, adapter, enabled, sort_order,
                  interval_sec, max_retries, retry_delay_sec)
               VALUES (?,?,?,?,?,?,?,?)
               ON CONFLICT(code) DO UPDATE SET
                 name=excluded.name, adapter=excluded.adapter, enabled=excluded.enabled,
                 sort_order=excluded.sort_order, interval_sec=excluded.interval_sec,
                 max_retries=excluded.max_retries, retry_delay_sec=excluded.retry_delay_sec""",
            (s.code, s.name, s.adapter, 1 if s.enabled else 0, s.sort_order,
             s.interval_sec, s.max_retries, s.retry_delay_sec),
        )

    # 配置中未列出的历史平台 → 停用（不删除，保留其历史留痕）
    known = [s.code for s in cfg.sources]
    removed = 0
    if known:
        cur = conn.execute(
            "UPDATE platform SET enabled=0 WHERE enabled=1 AND code NOT IN ("
            + ",".join("?" * len(known)) + ")", known)
        removed = cur.rowcount or 0
    conn.commit()

    enabled = sum(1 for s in cfg.sources if s.enabled)
    return {"configured": len(cfg.sources), "enabled": enabled, "disabled_removed": removed}


# ---------------------------------------------------------------- 看板写入 / 校验
def available_adapters() -> list[dict]:
    """可用适配器清单（代码 + 中文名），供看板「新增数据源」选择。"""
    from .adapters import ADAPTERS
    return [{"code": a.code, "name": a.name or a.code} for a in ADAPTERS.values()]


def _int(v, default, *, minimum: int, field: str) -> int:
    if v is None or v == "":
        return int(default)
    try:
        n = int(v)
    except (TypeError, ValueError):
        raise ValueError(f"{field} 必须是整数")
    if n < minimum:
        raise ValueError(f"{field} 不能小于 {minimum}")
    return n


def build(sources: list[dict], enrich_interval_sec=None, path: str | Path = CONFIG_PATH,
          trace_daily_quota=None) -> Config:
    """从看板提交的表单数据构建并校验 Config（适配器必须已实现，否则拒绝保存）。"""
    from .adapters import ADAPTERS
    if not isinstance(sources, list):
        raise ValueError("sources 必须是列表")
    out: list[Source] = []
    seen: set[str] = set()
    for i, item in enumerate(sources):
        if not isinstance(item, dict):
            raise ValueError(f"第 {i + 1} 项不是对象")
        code = str(item.get("code") or "").strip()
        if not code:
            raise ValueError(f"第 {i + 1} 项缺少 code")
        if code in seen:
            raise ValueError(f"重复的 code: {code}")
        seen.add(code)
        adapter = str(item.get("adapter") or code).strip()
        if adapter not in ADAPTERS:
            raise ValueError(f"{code}: 适配器 {adapter} 未实现（可选：{', '.join(sorted(ADAPTERS))}）")
        out.append(Source(
            code=code,
            name=str(item.get("name") or code),
            adapter=adapter,
            enabled=bool(item.get("enabled", True)),
            interval_sec=_int(item.get("interval_sec"), DEFAULTS["interval_sec"], minimum=5,
                              field=f"{code}.interval_sec"),
            max_retries=_int(item.get("max_retries"), DEFAULTS["max_retries"], minimum=0,
                             field=f"{code}.max_retries"),
            retry_delay_sec=_int(item.get("retry_delay_sec"), DEFAULTS["retry_delay_sec"], minimum=0,
                                 field=f"{code}.retry_delay_sec"),
            sort_order=_int(item.get("sort_order"), i + 1, minimum=0, field=f"{code}.sort_order"),
        ))
    enrich = _int(enrich_interval_sec, DEFAULTS["enrich_interval_sec"], minimum=10, field="enrich_interval_sec")
    quota = _int(trace_daily_quota, DEFAULTS["trace_daily_quota"], minimum=1, field="trace_daily_quota")
    return Config(sources=out, enrich_interval_sec=enrich, path=Path(path), trace_daily_quota=quota)


def dumps(cfg: Config) -> str:
    """把 Config 序列化为 TOML 文本（stdlib tomllib 只读，故手写序列化，保证可回读）。"""
    def q(s) -> str:
        return json.dumps(str(s), ensure_ascii=False)  # JSON 字符串即合法 TOML basic string

    lines = [
        "# =============================================================",
        "# 数据源注册表（定时采集的唯一事实来源）",
        "# -------------------------------------------------------------",
        "# 本文件由看板「数据源配置」页写入，也可手工编辑；代码与数据源解耦。",
        "#   · 停爬某平台：enabled 改 false（或整段注释/删除，同步时自动置停用）",
        "#   · 调抓取频率：interval_sec　· 加重试：max_retries / retry_delay_sec",
        "# 新增数据源 = 写一个适配器类 + 在下面加一段 [[source]]，无需改调度代码。",
        "# =============================================================",
        "",
        "[defaults]",
        "enabled             = true",
        f"interval_sec        = {DEFAULTS['interval_sec']}",
        f"max_retries         = {DEFAULTS['max_retries']}",
        f"retry_delay_sec     = {DEFAULTS['retry_delay_sec']}",
        f"enrich_interval_sec = {cfg.enrich_interval_sec}",
        f"trace_daily_quota   = {cfg.trace_daily_quota}",
        "",
        "# -------------------------------------------------------------",
        "# 平台清单（每项 enabled=false 即停爬）",
        "# -------------------------------------------------------------",
        "",
    ]
    for s in cfg.sources:
        lines += [
            "[[source]]",
            f"code            = {q(s.code)}",
            f"name            = {q(s.name)}",
            f"adapter         = {q(s.adapter)}",
            f"enabled         = {'true' if s.enabled else 'false'}",
            f"interval_sec    = {s.interval_sec}",
            f"max_retries     = {s.max_retries}",
            f"retry_delay_sec = {s.retry_delay_sec}",
            f"sort_order      = {s.sort_order}",
            "",
        ]
    return "\n".join(lines).rstrip("\n") + "\n"


def save(cfg: Config, path: str | Path | None = None) -> Path:
    """写回配置文件（默认写到 cfg.path）。"""
    p = Path(path) if path else cfg.path
    p.write_text(dumps(cfg), encoding="utf-8")
    return p
