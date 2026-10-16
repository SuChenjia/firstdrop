"""采集调度入口（CLI）。

用法（在项目根目录 firstdrop 下执行）：
  python -m app.runner --once                 # 采一轮全部平台并入库
  python -m app.runner --once --platforms weibo,zhihu
  python -m app.runner --loop --interval 300  # 每 300 秒采一轮
  python -m app.runner --report               # 打印采集健康度 + 各平台最新榜单
  python -m app.runner --prune 30             # 清理 30 天前的原始响应留痕
  python -m app.runner --trace --trace-limit 5  # 对已触发事件执行首发溯源
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from datetime import datetime, timedelta

from . import adapters, align, alerts, brief, db, fuse, mine, pipeline, split, trace
from . import config as src_config
from . import scheduler as scheduler_mod
from .normalize import normalize_title

# 每个平台保留的榜单条数上限（免费接口一般返回 30~50 条）
MAX_ITEMS = 50


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


# ---------------------------------------------------------------- 单平台采集
def collect_platform(conn, code: str) -> dict:
    started = now_iso()
    t0 = time.time()
    run_id = db.start_run(conn, code, started)

    http_status = None
    body_text = ""
    items: list[dict] = []
    status, error = "ok", None

    try:
        adapter = adapters.get(code)
        body, http_status = adapter.fetch()
        body_text = body.decode("utf-8", "replace")
        items = adapter.parse(body)[:MAX_ITEMS]
        for it in items:
            it["norm_title"] = normalize_title(it.get("title"))
        items = [it for it in items if it["norm_title"]]
        if not items:
            status = "empty"
    except Exception as e:  # noqa: BLE001
        status, error = "failed", f"{type(e).__name__}: {e}"

    duration_ms = int((time.time() - t0) * 1000)
    finished = now_iso()
    payload_hash = hashlib.sha256(body_text.encode("utf-8")).hexdigest() if body_text else None
    prev_hash = db.last_payload_hash(conn, code)
    changed = payload_hash != prev_hash

    if body_text:
        db.save_raw(conn, run_id, code, body_text, finished)
    count = db.insert_items(conn, run_id, code, finished, items) if items else 0

    db.finish_run(
        conn, run_id,
        status=status, http_status=http_status, item_count=count,
        duration_ms=duration_ms, payload_hash=payload_hash, changed=changed,
        error=error, finished_at=finished,
    )
    return {"code": code, "run_id": run_id, "status": status, "count": count,
            "http": http_status, "ms": duration_ms, "changed": changed, "error": error}


# ---------------------------------------------------------------- 一轮采集
def collect_with_retry(conn, code: str, max_retries: int = 0, retry_delay: int = 5) -> dict:
    """执行单个平台采集，失败按策略重试（总尝试 = 1 + max_retries）。"""
    attempt = 0
    while True:
        attempt += 1
        r = collect_platform(conn, code)
        if r["status"] != "failed" or attempt > max_retries:
            r["attempts"] = attempt
            return r
        print(f"    ↻ {code} 第 {attempt} 次失败（{r.get('error')}），{retry_delay}s 后重试"
              f"（剩余 {max_retries - attempt} 次）")
        time.sleep(retry_delay)


def run_cycle(conn, codes: list[str], max_retries: int = 0, retry_delay: int = 5) -> list[dict]:
    results = []
    for code in codes:
        r = collect_with_retry(conn, code, max_retries, retry_delay)
        flag = {"ok": "OK", "empty": "EMPTY", "failed": "FAIL"}.get(r["status"], r["status"])
        detail = f"{r['count']:>3} 条" if r["status"] != "failed" else (r["error"] or "")
        retry_note = f" · 重试{r['attempts']}次" if r.get("attempts", 1) > 1 else ""
        print(f"  [{flag:>5}] {code:<13} {detail}  ({r['ms']}ms)"
              + ("  · 榜单无变化" if r["status"] == "ok" and not r["changed"] else "")
              + retry_note)
        results.append(r)
    return results


# ---------------------------------------------------------------- 数据源配置
def bootstrap_config(conn, config_path=None):
    """加载 config/sources.toml 并同步进 platform 表；无配置文件时回落现有表。"""
    try:
        cfg = src_config.load(config_path or src_config.CONFIG_PATH)
    except FileNotFoundError as e:
        print(f"  [配置] 未找到 {e}，回落使用 platform 表内已启用清单")
        return None
    st = src_config.sync_platforms(conn, cfg)
    extra = f"，停用配置外 {st['disabled_removed']} 个" if st["disabled_removed"] else ""
    print(f"  [配置] 数据源 {st['configured']} 个（启用 {st['enabled']}）{extra} · {cfg.path}")
    missing = sorted({s.adapter for s in cfg.sources if s.adapter not in adapters.ADAPTERS})
    if missing:
        print(f"  [配置] 警告：以下 adapter 未实现，相关源无法采集 → {', '.join(missing)}")
    return cfg


def print_sources(conn, cfg) -> None:
    """打印数据源开关与调度参数（配置优先，否则读 platform 表）。"""
    print(f"\n=== 数据源（{cfg.path if cfg else 'platform 表'}）===")
    print(f"{'开关':<6}{'代码':<15}{'名称':<12}{'adapter':<15}{'间隔s':>7}{'重试':>5}{'重试等待s':>10}")
    if cfg:
        for s in cfg.sources:
            print(f"{'启用' if s.enabled else '停用':<6}{s.code:<15}{s.name:<12}"
                  f"{s.adapter:<15}{s.interval_sec:>7}{s.max_retries:>5}{s.retry_delay_sec:>10}")
    else:
        for r in conn.execute("SELECT * FROM platform ORDER BY sort_order"):
            print(f"{'启用' if r['enabled'] else '停用':<6}{r['code']:<15}{r['name']:<12}"
                  f"{r['adapter']:<15}{r['interval_sec']:>7}{r['max_retries']:>5}{r['retry_delay_sec']:>10}")
    n_on = (len(cfg.enabled()) if cfg else
            conn.execute("SELECT COUNT(*) c FROM platform WHERE enabled=1").fetchone()["c"])
    total = (len(cfg.sources) if cfg else
             conn.execute("SELECT COUNT(*) c FROM platform").fetchone()["c"])
    print(f"  合计 {total} 个 · 启用 {n_on} 个（改 config/sources.toml 即可开关/调间隔，无需改代码）")


def enabled_platforms(conn) -> list[str]:
    rows = conn.execute(
        "SELECT code FROM platform WHERE enabled=1 ORDER BY sort_order"
    ).fetchall()
    return [r["code"] for r in rows]


# ---------------------------------------------------------------- 加工：对齐 + 预警
def enrich(conn, rebuild: bool = False) -> dict:
    """采集完成后执行：盘点拆分 → 统一标准表构建 → 跨平台话题对齐 → 实体归一共热 → 爆点预警检测。"""
    sp = split.expand(conn, mode="all" if rebuild else "latest")
    print(f"  [拆分] 聚合/盘点条目 {sp['aggregates']} 条 → 拆出子话题 {sp['children']} 条"
          f"（去重丢弃 {sp['dropped']} 条；父条目已在归一化/对齐/预警中排除）")
    pm = pipeline.build(conn, mode="all" if rebuild else "latest")
    print(f"  [加工] 统一标准表 word_metric {pm['rows']} 行 / {pm['boards']} 个榜单"
          f"（过滤 {pm['filtered']} 条）")
    a = align.align(conn)
    print(f"  [对齐] {a['topics']} 个话题 → {a['clusters']} 簇（其中跨平台 {a['multi']} 簇"
          f" · 别名命中 {a.get('alias_hits', 0)} 条）")
    fz = fuse.fuse(conn)
    print(f"  [融合] 实体归一 {fz['entities']} 个实体（其中跨平台 {fz['multi']} 实体）"
          f"· 双口径聚合热度已写入 entity_metric")
    al = alerts.detect(conn)
    print(f"  [预警] 命中 {al['candidates']} 个候选，新增 {al['new']} 条"
          f"（话题 {al['topic']} / 跨平台 {al['cluster']}）")
    mn = mine.run(conn)
    print(f"  [别名] 扫描 {mn['scanned']} 项 → 新增候选 {mn['new']} 条"
          f"（pending 合计 {mn['pending_total']} 条；其中跨平台有效 {mn['impact_active']} 条，"
          f"余为当前影响 0 的低优先级；待人工审核）")
    tr = trace.register_triggers(conn)
    print(f"  [溯源] 触发扫描 {tr['scanned']} 簇 → 新登记 {tr['new']} 个事件"
          f"（L1 {tr['l1']} / L2 {tr['new'] - tr['l1']}；待溯源，执行 `--trace` 或看板触发）")
    return {**pm, **a, **fz, **al, **mn, **tr}


# ---------------------------------------------------------------- 报表
def print_report(conn) -> None:
    print("\n=== 采集健康度 ===")
    print(f"{'平台':<14}{'总批次':>6}{'成功':>6}{'失败':>6}{'均耗时ms':>10}  最近采集")
    for r in conn.execute("SELECT * FROM v_run_health ORDER BY platform_code"):
        print(f"{r['platform_code']:<14}{r['total_runs']:>6}{r['ok_runs']:>6}"
              f"{r['failed_runs']:>6}{(r['avg_ms'] or 0):>10}  {r['last_run'] or '-'}")

    print("\n=== 各平台最新榜单（Top 10） ===")
    for code in enabled_platforms(conn):
        rows = conn.execute(
            "SELECT position, is_top, title, hot_value FROM v_latest_item "
            "WHERE platform_code=? ORDER BY position LIMIT 10", (code,)
        ).fetchall()
        if not rows:
            print(f"\n[{code}] 暂无数据")
            continue
        print(f"\n[{code}]")
        for r in rows:
            hot = f"{r['hot_value']:,}" if r["hot_value"] is not None else "-"
            pin = "置顶 " if r["is_top"] else ""
            print(f"  {r['position']:>2}. {pin}{r['title'][:40]}  [{hot}]")

    print("\n=== 历史留痕规模 ===")
    total_items = conn.execute("SELECT COUNT(*) c FROM hot_item").fetchone()["c"]
    total_topics = conn.execute(
        "SELECT COUNT(*) c FROM (SELECT 1 FROM hot_item GROUP BY platform_code, norm_title)"
    ).fetchone()["c"]
    total_runs = conn.execute("SELECT COUNT(*) c FROM collect_run").fetchone()["c"]
    print(f"  采集批次 {total_runs} · 榜单条目 {total_items} · 去重话题 {total_topics}")

    print("\n=== 跨平台话题（≥2 平台，Top 8） ===")
    rows = conn.execute(
        "SELECT label, platforms, platform_count, peak_hot FROM v_cluster "
        "WHERE platform_count>=2 ORDER BY platform_count DESC, peak_hot DESC LIMIT 8"
    ).fetchall()
    if not rows:
        print("  （暂无：需同一事件被多个平台同时收录）")
    for r in rows:
        print(f"  [{r['platform_count']} 平台] {r['label'][:38]}  平台={r['platforms']}  峰值={r['peak_hot'] or '-'}")

    print("\n=== 统一标准表 · 跨平台归一化热度 Top 12 ===")
    rows = conn.execute(
        "SELECT 词, 来源平台, 热度口径, 热度原始值, 归一化热度, 环比增长率, 分类标签, 趋势标记 "
        "FROM v_unified_word ORDER BY 归一化热度 DESC LIMIT 12"
    ).fetchall()
    if not rows:
        print("  （暂无：请先执行加工/重建）")
    for r in rows:
        g = r["环比增长率"]
        gs = f"{g*100:+.0f}%" if g is not None else "  —"
        print(f"  {r['归一化热度']:>5}  {r['来源平台']:<13}{r['分类标签']:<4}"
              f"{r['趋势标记']:<4} {r['热度口径']:<6}{r['词'][:28]:<28} {gs}")

    print("\n=== 分类分布（统一标准表） ===")
    rows = conn.execute(
        "SELECT 分类标签 AS c, COUNT(*) AS n FROM v_unified_word GROUP BY 分类标签 ORDER BY n DESC"
    ).fetchall()
    if not rows:
        print("  （暂无）")
    else:
        print("  " + " · ".join(f"{r['c']} {r['n']}" for r in rows))

    print("\n=== 实体归一 · 跨平台同词融合 Top 10（双口径） ===")
    rows = conn.execute(
        "SELECT 实体, 分类标签, 聚合热度_峰值式, 聚合热度_联合式, 覆盖平台数, 覆盖平台, "
        "单平台峰值, 扩散加成 FROM v_unified_entity "
        "ORDER BY 聚合热度_联合式 DESC LIMIT 10"
    ).fetchall()
    if not rows:
        print("  （暂无：请先执行加工/重建）")
    for r in rows:
        print(f"  [{r['覆盖平台数']}平台] {r['实体'][:26]:<26} {r['分类标签']:<4}"
              f"峰值式={r['聚合热度_峰值式']:>5} 联合式={r['聚合热度_联合式']:>5} "
              f"(单平台峰值={r['单平台峰值']} 扩散+{r['扩散加成']:.0f})  {r['覆盖平台']}")

    print("\n=== 爆点预警（最近 10 条） ===")
    rows = conn.execute(
        "SELECT created_at, level, scope, title, score FROM alert "
        "ORDER BY created_at DESC, score DESC LIMIT 10"
    ).fetchall()
    if not rows:
        print("  （暂无预警）")
    for r in rows:
        tag = {"high": "高", "medium": "中", "low": "低"}.get(r["level"], r["level"])
        scope = "跨平台" if r["scope"] == "cluster" else "单平台"
        print(f"  [{tag}][{scope}] {r['title'][:36]}  score={r['score']:.0f}  {r['created_at']}")


# ---------------------------------------------------------------- 留痕清理
def prune(conn, days: int) -> None:
    cutoff = (datetime.now().astimezone() - timedelta(days=days)).isoformat(timespec="seconds")
    conn.execute("DELETE FROM raw_payload WHERE captured_at < ?", (cutoff,))
    conn.commit()
    print(f"已清理 {days} 天前的原始响应留痕（榜单事实表 hot_item 永久保留）。")


# ---------------------------------------------------------------- main
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="热点聚合 MVP 采集器")
    ap.add_argument("--db", default=str(db.DEFAULT_DB), help="SQLite 路径")
    ap.add_argument("--platforms", default="all", help="逗号分隔的平台代码，默认 all")
    ap.add_argument("--once", action="store_true", help="采集一轮后退出")
    ap.add_argument("--loop", action="store_true", help="循环采集（全局固定间隔）")
    ap.add_argument("--schedule", action="store_true",
                    help="按数据源独立间隔/开关/重试调度采集（配置见 config/sources.toml）")
    ap.add_argument("--sources", action="store_true", help="打印数据源开关与调度配置后退出")
    ap.add_argument("--config", default=None, help="数据源配置文件路径（默认 config/sources.toml）")
    ap.add_argument("--interval", type=int, default=300, help="循环间隔秒数（默认 300）")
    ap.add_argument("--report", action="store_true", help="打印报表后退出")
    ap.add_argument("--rebuild", action="store_true",
                    help="清空并全量重建统一标准表 word_metric（历史回填，环比最准）后再对齐/预警")
    ap.add_argument("--prune", type=int, metavar="DAYS", help="清理 N 天前的原始响应")
    ap.add_argument("--mine", action="store_true",
                    help="自动挖掘别名候选，写入 entity_alias_suggest（pending，待审核）")
    ap.add_argument("--trace", action="store_true",
                    help="对已触发事件执行首发溯源（多源检索 + 通用正文抽取）")
    ap.add_argument("--trace-limit", type=int, default=5,
                    help="本次溯源处理的事件数上限（默认 5，受每日预算约束）")
    ap.add_argument("--no-enrich", action="store_true", help="跳过跨平台对齐与爆点预警")
    ap.add_argument("--brief", action="store_true",
                    help="调度模式启用每日简报任务（默认 23:30 生成）")
    args = ap.parse_args(argv)

    conn = db.connect(args.db)
    db.init_db(conn)

    # 数据源配置（开关/间隔/重试的唯一事实来源）→ 同步进 platform 表
    cfg = bootstrap_config(conn, args.config)

    if args.sources:
        print_sources(conn, cfg)
        return 0

    if args.report:
        print_report(conn)
        return 0

    if args.prune:
        prune(conn, args.prune)
        return 0

    if args.mine:
        print(f"\n[{now_iso()}] 自动挖掘别名词典候选")
        m = mine.run(conn)
        print(f"  扫描候选项 {m['scanned']} 个 → 新增 {m['new']} 条（去重后）"
              f" · pending 合计 {m['pending_total']} 条")
        print(f"  来源分布：{m['by_source']}")
        reqs = conn.execute(
            "SELECT alias_norm, canonical, source, evidence FROM entity_alias_suggest "
            "WHERE status='pending' ORDER BY created_at DESC, alias_norm LIMIT 20").fetchall()
        print("  样例（前 20）：")
        for r in reqs:
            print(f"    [{r['source']}] {r['alias_norm']} → {r['canonical']}"
                  f"   ({r['evidence'][:46]})")
        return 0

    if args.trace:
        print(f"\n[{now_iso()}] 事件首发溯源")
        tr = trace.register_triggers(conn)
        print(f"  触发登记：扫描 {tr['scanned']} 簇 → 新增 {tr['new']} 个待溯源事件")
        st = trace.run(conn, limit=args.trace_limit)
        if st["skipped_quota"]:
            print(f"  今日预算已用尽（{st['quota_used']}/{st['quota']}），跳过溯源")
        else:
            print(f"  溯源 {st['traced']} 个事件 → 产出首发信号 {st['signals']} 条"
                  f"（今日预算 {st['quota_used'] + st['traced']}/{st['quota']}）")
        rows = conn.execute(
            "SELECT cluster_id, title, tier, status, earliest_site, earliest_posted_at, confidence "
            "FROM v_event_track WHERE earliest_url IS NOT NULL "
            "ORDER BY confidence DESC, earliest_posted_at LIMIT 20").fetchall()
        if rows:
            print("  首发信号（按置信度）：")
            for r in rows:
                print(f"    [簇{r['cluster_id']}][{r['tier']}] {r['title'][:22]:<22} "
                      f"{r['earliest_posted_at'] or '时间未知'}  {r['earliest_site'] or '?'}  "
                      f"置信={r['confidence']}")
        else:
            print("  （暂无可用的首发信号；可能搜索源无命中或链接抽取失败）")
        return 0

    if args.rebuild:
        print(f"\n[{now_iso()}] 全量重建统一标准表")
        enrich(conn, rebuild=True)
        print_report(conn)
        return 0

    # ---- 定时调度模式：每个数据源独立间隔 / 开关 / 失败重试 ----
    if args.schedule:
        sources = (cfg.enabled() if cfg else
                   [src_config.Source(code=r["code"], name=r["name"], adapter=r["adapter"],
                                      enabled=True, interval_sec=args.interval, max_retries=0,
                                      retry_delay_sec=5, sort_order=r["sort_order"])
                    for r in conn.execute("SELECT * FROM platform WHERE enabled=1 ORDER BY sort_order")])
        if args.platforms != "all":
            allow = {c.strip() for c in args.platforms.split(",") if c.strip()}
            sources = [s for s in sources if s.code in allow]
        if not sources:
            print("没有启用的数据源（检查 config/sources.toml 的 enabled）")
            return 1

        print(f"\n[{now_iso()}] 定时调度启动（Ctrl-C 退出）")
        print_sources(conn, cfg)
        enrich_iv = cfg.enrich_interval_sec if cfg else 600

        def on_result(src, res, attempts, final):
            if final:
                flag = {"ok": "OK", "empty": "EMPTY", "failed": "FAIL"}.get(res.get("status"), res.get("status"))
                detail = f"{res.get('count', 0):>3} 条" if res.get("status") != "failed" else (res.get("error") or "")
                note = f" · 重试{attempts - 1}次" if attempts > 1 else ""
                print(f"  [{now_iso()}] [{flag:>5}] {src.code:<13} {detail}{note}")

        sched = scheduler_mod.Scheduler(
            sources,
            collect_fn=lambda code: collect_platform(conn, code),
            tick=min([s.interval_sec for s in sources] + [5]),
            retry=True,
            enrich_fn=(None if args.no_enrich else (lambda: enrich(conn))),
            enrich_interval=enrich_iv,
            on_result=on_result,
            daily_fn=(lambda: brief.generate()) if args.brief else None,
            daily_hour=23, daily_minute=30,
        )
        try:
            sched.run_forever()
        except KeyboardInterrupt:
            print("\n已停止。运行统计：")
            for code, st in sched.stats().items():
                print(f"  {code:<14} 采集 {st['runs']} 次 · 失败 {st['failures']} 次")
            return 0

    codes = enabled_platforms(conn) if args.platforms == "all" else [
        c.strip() for c in args.platforms.split(",") if c.strip()
    ]

    print(f"数据库: {args.db}")
    print(f"平台: {', '.join(codes)}")
    max_retries = cfg.sources[0].max_retries if cfg and cfg.sources else 0
    retry_delay = cfg.sources[0].retry_delay_sec if cfg and cfg.sources else 5

    if args.loop:
        print(f"循环模式，每 {args.interval}s 采一轮（Ctrl-C 退出）")
        try:
            while True:
                print(f"\n[{now_iso()}] 开始采集")
                run_cycle(conn, codes, max_retries, retry_delay)
                if not args.no_enrich:
                    enrich(conn)
                time.sleep(args.interval)
        except KeyboardInterrupt:
            print("\n已停止。")
            return 0

    print(f"\n[{now_iso()}] 开始采集")
    run_cycle(conn, codes, max_retries, retry_delay)
    if not args.no_enrich:
        enrich(conn)
    print_report(conn)
    return 0


if __name__ == "__main__":
    sys.exit(main())
