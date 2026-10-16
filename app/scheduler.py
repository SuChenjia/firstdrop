"""按数据源间隔调度的定时采集引擎（与具体抓取逻辑解耦）。

调度器只关心三件事：**谁（Source）、隔多久、失败重试几次**；它不碰 HTTP/解析细节——
真正的抓取通过 `collect_fn(code)` 注入（由 runner 传入）。因此"抓什么、阈值多少"
全由 config/sources.toml 决定，调度代码无需随平台增减而改动。

用法：
    sched = Scheduler(cfg.enabled(), collect_fn=collect_fn, retry=True,
                      on_result=report, enrich_fn=enrich,
                      enrich_interval=cfg.enrich_interval_sec)
    sched.run_forever()
"""
from __future__ import annotations

import time
from dataclasses import dataclass

from .config import Source


@dataclass
class RunState:
    """单源的运行态（下次到期时刻、累计次数）。"""

    code: str
    next_due: float
    runs: int = 0
    failures: int = 0
    attempts: int = 0


class Scheduler:
    def __init__(
        self,
        sources: list[Source],
        collect_fn,
        *,
        tick: float = 5.0,
        retry: bool = True,
        enrich_fn=None,
        enrich_interval: int = 600,
        on_result=None,
        clock=time.monotonic,
        sleep=time.sleep,
        daily_fn=None,
        daily_hour: int = 23,
        daily_minute: int = 30,
    ):
        self.sources = list(sources)
        self._collect = collect_fn
        self.tick_sec = max(0.1, tick)
        self.retry = retry
        self._enrich_fn = enrich_fn
        self.enrich_interval = enrich_interval
        self._on_result = on_result
        self._clock = clock
        self._sleep = sleep
        self._daily_fn = daily_fn
        self._daily_hour = daily_hour
        self._daily_minute = daily_minute
        self._last_daily: str | None = None  # "YYYY-MM-DD" 防止同日重复触发
        self._states: list[RunState] = []
        self._last_enrich = float("-inf")
        self._pending_enrich = False

    # ---------------------------------------------------------- 主循环
    def run_forever(self) -> None:
        """立即首采一轮，随后按各源 interval 到期触发（阻塞；由调用方捕获 Ctrl-C）。"""
        now = self._clock()
        self._states = [RunState(s.code, now) for s in self.sources]
        while True:
            self.tick()
            self._sleep(self.tick_sec)

    def tick(self, now: float | None = None) -> int:
        """触发所有到期源；返回本轮触发的源数量（可被测试直接调用）。"""
        now = self._clock() if now is None else now
        fired = 0
        for st, src in zip(self._states, self.sources):
            if st.next_due <= now:
                self._fire(st, src)
                fired += 1
        if fired:
            if self._enrich_fn and (now - self._last_enrich) >= self.enrich_interval:
                self._last_enrich = now
                self._enrich_fn()
        # 每日定时任务（如 23:30 生成简报）
        if self._daily_fn:
            self._check_daily()
        return fired

    def _check_daily(self) -> None:
        """检查是否到达每日任务触发时刻（本地时间），同日只触发一次。"""
        from datetime import datetime
        now = datetime.now().astimezone()
        today_str = now.strftime("%Y-%m-%d")
        if (now.hour == self._daily_hour and now.minute >= self._daily_minute
                and self._last_daily != today_str):
            self._last_daily = today_str
            try:
                self._daily_fn()
            except Exception as e:  # noqa: BLE001
                print(f"  [scheduler] 每日任务执行失败: {type(e).__name__}: {e}")

    # ---------------------------------------------------------- 单源采集 + 重试
    def _fire(self, st: RunState, src: Source) -> None:
        attempts = 0
        st.runs += 1
        while True:
            attempts += 1
            res = self._collect(src.code) or {}
            st.attempts += 1
            failed = res.get("status") == "failed"
            # 重试：失败且还有重试余额
            if failed and self.retry and attempts <= src.max_retries:
                self._notify(src, res, attempts, final=False)
                self._sleep(src.retry_delay_sec)
                continue
            if failed:
                st.failures += 1
            break
        self._notify(src, res, attempts, final=True)
        # 下一次到期：以"本次结束时间 + interval"计，避免长任务/重试造成时间堆积
        st.next_due = self._clock() + src.interval_sec

    def _notify(self, src: Source, res: dict, attempts: int, *, final: bool) -> None:
        if self._on_result:
            self._on_result(src, res, attempts, final)

    def stats(self) -> dict:
        return {
            st.code: {"runs": st.runs, "failures": st.failures, "attempts": st.attempts,
                      "next_due_in": round(st.next_due - self._clock(), 1)}
            for st in self._states
        }
