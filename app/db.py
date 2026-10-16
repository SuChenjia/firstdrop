"""SQLite 访问层：连接、初始化、采集入库。"""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path

SCHEMA_PATH = Path(__file__).with_name("schema.sql")
DEFAULT_DB = Path(__file__).resolve().parent.parent / "data" / "hotspot.db"


def now_iso() -> str:
    """本地时区的 ISO8601 时间戳（秒级）。"""
    return datetime.now().astimezone().isoformat(timespec="seconds")


def connect(db_path: str | os.PathLike = DEFAULT_DB) -> sqlite3.Connection:
    """打开（必要时创建）数据库，启用 WAL 与外键约束。"""
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    """建表（幂等），并对旧库补齐新增列。"""
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    _ensure_column(conn, "topic_cluster", "matched_alias", "TEXT")
    # 别名候选的跨平台影响（反事实评估，见 align.alias_impact）
    _ensure_column(conn, "entity_alias_suggest", "impact_topics", "INTEGER")
    _ensure_column(conn, "entity_alias_suggest", "impact_platforms", "INTEGER")
    # 数据源调度（见 config.py，platform 为配置的只读镜像）
    _ensure_column(conn, "platform", "interval_sec", "INTEGER NOT NULL DEFAULT 300")
    _ensure_column(conn, "platform", "max_retries", "INTEGER NOT NULL DEFAULT 2")
    _ensure_column(conn, "platform", "retry_delay_sec", "INTEGER NOT NULL DEFAULT 5")
    # 盘点/聚合条目处理（见 split.py）
    _ensure_column(conn, "hot_item", "source", "TEXT NOT NULL DEFAULT 'raw'")
    _ensure_column(conn, "hot_item", "is_aggregate", "INTEGER NOT NULL DEFAULT 0")
    _ensure_column(conn, "hot_item", "aggregate_reason", "TEXT")
    _ensure_column(conn, "hot_item", "parent_norm_title", "TEXT")
    _ensure_column(conn, "hot_item", "seg_index", "INTEGER")
    _ensure_column(conn, "word_metric", "source", "TEXT NOT NULL DEFAULT 'raw'")
    conn.commit()


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, decl: str) -> None:
    """对已存在的旧库补齐新增列（SQLite 的 CREATE TABLE IF NOT EXISTS 不会加列）。"""
    cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
    if cols and column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


# ---------------------------------------------------------------- 写入
def start_run(conn: sqlite3.Connection, platform_code: str, started_at: str) -> int:
    cur = conn.execute(
        "INSERT INTO collect_run (platform_code, started_at, status) VALUES (?, ?, 'running')",
        (platform_code, started_at),
    )
    conn.commit()
    return cur.lastrowid


def finish_run(
    conn: sqlite3.Connection,
    run_id: int,
    *,
    status: str,
    http_status: int | None,
    item_count: int,
    duration_ms: int,
    payload_hash: str | None,
    changed: bool,
    error: str | None,
    finished_at: str,
) -> None:
    conn.execute(
        """UPDATE collect_run
              SET status=?, http_status=?, item_count=?, duration_ms=?,
                  payload_hash=?, changed=?, error=?, finished_at=?
            WHERE id=?""",
        (status, http_status, item_count, duration_ms,
         payload_hash, 1 if changed else 0, error, finished_at, run_id),
    )
    conn.commit()


def save_raw(conn: sqlite3.Connection, run_id: int, platform_code: str, body: str, captured_at: str) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO raw_payload (run_id, platform_code, body, length, captured_at) VALUES (?,?,?,?,?)",
        (run_id, platform_code, body, len(body or ""), captured_at),
    )
    conn.commit()


def insert_items(conn: sqlite3.Connection, run_id: int, platform_code: str, captured_at: str, items: list[dict]) -> int:
    """批量写入榜单条目（历史留痕事实表）。"""
    rows = [
        (
            run_id,
            platform_code,
            it.get("position"),
            1 if it.get("is_top") else 0,
            it.get("title") or "",
            it.get("norm_title") or "",
            it.get("external_id"),
            it.get("url"),
            it.get("hot_value"),
            it.get("hot_text"),
            it.get("label"),
            it.get("author"),
            json.dumps(it.get("extra"), ensure_ascii=False) if it.get("extra") else None,
            captured_at,
        )
        for it in items
    ]
    if not rows:
        return 0
    conn.executemany(
        """INSERT INTO hot_item
             (run_id, platform_code, position, is_top, title, norm_title, external_id,
              url, hot_value, hot_text, label, author, extra_json, captured_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        rows,
    )
    conn.commit()
    return len(rows)


def last_payload_hash(conn: sqlite3.Connection, platform_code: str) -> str | None:
    row = conn.execute(
        "SELECT payload_hash FROM collect_run WHERE platform_code=? AND payload_hash IS NOT NULL "
        "ORDER BY id DESC LIMIT 1",
        (platform_code,),
    ).fetchone()
    return row["payload_hash"] if row else None
