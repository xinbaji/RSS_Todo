"""SQLite 存储层：待办清单 / 已见历史 / 下载任务 / 监控最新值。"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

STATUS_TODO = "todo"
STATUS_DONE = "done"
STATUS_IGNORED = "ignored"
VALID_STATUS = (STATUS_TODO, STATUS_DONE, STATUS_IGNORED)

DL_PENDING = "pending"
DL_RUNNING = "running"
DL_SUCCESS = "success"
DL_FAILED = "failed"
DL_CANCELED = "canceled"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
  id                INTEGER PRIMARY KEY AUTOINCREMENT,
  subscription_id   TEXT NOT NULL,
  video_id          TEXT NOT NULL,
  title             TEXT NOT NULL,
  url               TEXT NOT NULL,
  cover             TEXT DEFAULT '',
  author            TEXT DEFAULT '',
  published_at      INTEGER DEFAULT 0,
  matched_keywords  TEXT DEFAULT '[]',
  status            TEXT NOT NULL DEFAULT 'todo',
  created_at        INTEGER NOT NULL,
  done_at           INTEGER DEFAULT 0,
  UNIQUE (subscription_id, video_id)
);
CREATE INDEX IF NOT EXISTS idx_items_status ON items(status);
CREATE INDEX IF NOT EXISTS idx_items_sub ON items(subscription_id);

CREATE TABLE IF NOT EXISTS seen_videos (
  subscription_id   TEXT NOT NULL,
  video_id          TEXT NOT NULL,
  first_seen_at     INTEGER NOT NULL,
  PRIMARY KEY (subscription_id, video_id)
);

CREATE TABLE IF NOT EXISTS downloads (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  item_id       INTEGER,
  video_id      TEXT NOT NULL,
  title         TEXT NOT NULL,
  url           TEXT NOT NULL,
  status        TEXT NOT NULL DEFAULT 'pending',
  content_type  TEXT NOT NULL DEFAULT 'video',
  quality       TEXT NOT NULL DEFAULT 'best',
  save_dir      TEXT NOT NULL,
  file_path     TEXT DEFAULT '',
  progress      REAL DEFAULT 0,
  error         TEXT DEFAULT '',
  created_at    INTEGER NOT NULL,
  started_at    INTEGER DEFAULT 0,
  finished_at   INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS monitor_values (
  rule_id       TEXT PRIMARY KEY,
  value         TEXT DEFAULT '',
  fetched_at    INTEGER DEFAULT 0,
  last_error    TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS meta (
  key           TEXT PRIMARY KEY,
  value         TEXT
);
"""


class Storage:
    def __init__(self, data_dir: str | Path = "data"):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.data_dir / "app.db"
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()
        self._lock = __import__("threading").Lock()

    def close(self) -> None:
        try:
            self._conn.close()
        except Exception:
            pass

    # ---------- 待办清单 ----------
    def add_item(self, sub_id: str, video: dict, matched: list[str]) -> bool:
        """新增清单条目（该视频在待办已存在则跳过，全局按 video_id 去重），返回是否新增。

        全局去重：同一视频（bvid / bvid_pN）无论来自哪个订阅，待办只出现一次。
        """
        with self._lock:
            cur = self._conn.execute(
                "SELECT 1 FROM items WHERE video_id=?", (video["video_id"],))
            if cur.fetchone():
                return False
            self._conn.execute(
                """INSERT INTO items
                   (subscription_id, video_id, title, url, cover, author, published_at,
                    matched_keywords, status, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (sub_id, video["video_id"], video["title"], video["url"],
                 video.get("cover", ""), video.get("author", ""),
                 int(video.get("published_at", 0) or 0),
                 json.dumps(matched, ensure_ascii=False), STATUS_TODO, int(time.time())),
            )
            self._conn.commit()
            return True

    def mark_seen(self, sub_id: str, video_id: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR IGNORE INTO seen_videos (subscription_id, video_id, first_seen_at) VALUES (?,?,?)",
                (sub_id, video_id, int(time.time())),
            )
            self._conn.commit()

    def is_seen(self, sub_id: str, video_id: str) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "SELECT 1 FROM seen_videos WHERE subscription_id=? AND video_id=?",
                (sub_id, video_id),
            )
            return cur.fetchone() is not None

    def reset_history(self, sub_id: str) -> None:
        """清空某订阅的已见历史（用于关键词修改后重新扫描）。"""
        with self._lock:
            self._conn.execute("DELETE FROM seen_videos WHERE subscription_id=?", (sub_id,))
            self._conn.commit()

    def set_status(self, item_id: int, status: str) -> bool:
        if status not in VALID_STATUS:
            return False
        with self._lock:
            cur = self._conn.execute("SELECT 1 FROM items WHERE id=?", (item_id,))
            if not cur.fetchone():
                return False
            done_at = int(time.time()) if status == STATUS_DONE else 0
            self._conn.execute(
                "UPDATE items SET status=?, done_at=? WHERE id=?", (status, done_at, item_id))
            self._conn.commit()
            return True

    def clear_subscription(self, sub_id: str) -> None:
        """删除订阅时级联清理：该订阅下所有待办条目与已见历史。"""
        with self._lock:
            self._conn.execute("DELETE FROM items WHERE subscription_id=?", (sub_id,))
            self._conn.execute("DELETE FROM seen_videos WHERE subscription_id=?", (sub_id,))
            self._conn.commit()

    def delete_item(self, item_id: int) -> bool:
        """从清单硬删除，同时删除该条目的已见历史——下次刷新会重新加入。

        语义：删除=彻底移除；忽略=保留已见历史（刷新不打扰）。
        """
        with self._lock:
            row = self._conn.execute(
                "SELECT subscription_id, video_id FROM items WHERE id=?", (item_id,)).fetchone()
            if row:
                self._conn.execute(
                    "DELETE FROM seen_videos WHERE subscription_id=? AND video_id=?",
                    (row[0], row[1]))
            cur = self._conn.execute("DELETE FROM items WHERE id=?", (item_id,))
            self._conn.commit()
            return cur.rowcount > 0

    def list_items(self, status: str | None = None,
                   sub_ids: str | list[str] | None = None) -> list[dict]:
        """查询清单。sub_ids：单个订阅 id 或 id 列表（列表=同名/多订阅合并显示）。"""
        sql = "SELECT * FROM items"
        params: tuple = ()
        conds = []
        if status and status != "all":
            conds.append("status=?")
            params = (*params, status)
        if sub_ids:
            if isinstance(sub_ids, str):
                sub_ids = [sub_ids]
            placeholders = ",".join("?" * len(sub_ids))
            conds.append(f"subscription_id IN ({placeholders})")
            params = (*params, *sub_ids)
        if conds:
            sql += " WHERE " + " AND ".join(conds)
        sql += " ORDER BY published_at DESC, id DESC"
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [self._row_item(r) for r in rows]

    def get_item(self, item_id: int) -> dict | None:
        with self._lock:
            r = self._conn.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
        return self._row_item(r) if r else None

    def item_stats(self) -> dict:
        with self._lock:
            rows = self._conn.execute(
                "SELECT status, COUNT(*) AS n FROM items GROUP BY status").fetchall()
        out = {STATUS_TODO: 0, STATUS_DONE: 0, STATUS_IGNORED: 0}
        for r in rows:
            if r["status"] in out:
                out[r["status"]] = r["n"]
        out["total"] = sum(out.values())
        return out

    @staticmethod
    def _row_item(r: sqlite3.Row) -> dict:
        d = dict(r)
        try:
            d["matched_keywords"] = json.loads(d.get("matched_keywords") or "[]")
        except (json.JSONDecodeError, TypeError):
            d["matched_keywords"] = []
        return d

    # ---------- 下载任务 ----------
    def add_download(self, item: dict, content_type: str, quality: str,
                     save_dir: str) -> int:
        with self._lock:
            cur = self._conn.execute(
                """INSERT INTO downloads
                   (item_id, video_id, title, url, status, content_type, quality,
                    save_dir, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (item.get("id"), item["video_id"], item["title"], item["url"],
                 DL_PENDING, content_type, quality, save_dir, int(time.time())),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def update_download(self, dl_id: int, **fields) -> None:
        allowed = {"status", "file_path", "progress", "error", "started_at", "finished_at"}
        sets, params = [], []
        for k, v in fields.items():
            if k in allowed:
                sets.append(f"{k}=?")
                params.append(v)
        if not sets:
            return
        params.append(dl_id)
        with self._lock:
            self._conn.execute(f"UPDATE downloads SET {', '.join(sets)} WHERE id=?",
                               tuple(params))
            self._conn.commit()

    def list_downloads(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM downloads ORDER BY id DESC").fetchall()
        return [dict(r) for r in rows]

    def get_download(self, dl_id: int) -> dict | None:
        with self._lock:
            r = self._conn.execute("SELECT * FROM downloads WHERE id=?", (dl_id,)).fetchone()
        return dict(r) if r else None

    def delete_download(self, dl_id: int) -> bool:
        with self._lock:
            cur = self._conn.execute("DELETE FROM downloads WHERE id=?", (dl_id,))
            self._conn.commit()
            return cur.rowcount > 0

    # ---------- 通用 KV（持久化调度状态等） ----------
    def set_meta(self, key: str, value: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO meta (key, value) VALUES (?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, str(value)),
            )
            self._conn.commit()

    def get_meta(self, key: str, default: str = "") -> str:
        with self._lock:
            cur = self._conn.execute("SELECT value FROM meta WHERE key=?", (key,))
            row = cur.fetchone()
        return row["value"] if row else default

    # ---------- 监控值 ----------
    def set_monitor_value(self, rule_id: str, value: str, fetched_at: int,
                          last_error: str = "") -> None:
        with self._lock:
            self._conn.execute(
                """INSERT INTO monitor_values (rule_id, value, fetched_at, last_error)
                   VALUES (?,?,?,?)
                   ON CONFLICT(rule_id) DO UPDATE SET
                     value=excluded.value, fetched_at=excluded.fetched_at,
                     last_error=excluded.last_error""",
                (rule_id, value, fetched_at, last_error),
            )
            self._conn.commit()

    def monitor_values(self) -> dict[str, dict]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM monitor_values").fetchall()
        return {r["rule_id"]: dict(r) for r in rows}
