"""调度器：定时 + 手动刷新订阅与监控，规则级防重入。"""
from __future__ import annotations

import logging
import threading
import time

from .adapters import create_adapter
from .matcher import is_excluded, match_keywords

log = logging.getLogger("rss-todo.scheduler")


class Scheduler:
    def __init__(self, storage, subs, config):
        self.storage = storage
        self.subs = subs
        self.config = config
        self._last_refresh: dict[str, float] = {}
        self._running: set[str] = set()
        self._lock = threading.Lock()
        self._stop = False
        self._thread: threading.Thread | None = None
        self._monitor_hook = None  # M7 挂载监控检查函数
        self._load_persisted_state()

    def _load_persisted_state(self) -> None:
        """重启后从 SQLite 恢复各订阅上次刷新时间，避免全部立即重刷。"""
        for sub in self.subs.all():
            v = self.storage.get_meta(f"last_refresh:{sub['id']}")
            if v:
                try:
                    self._last_refresh[sub["id"]] = float(v)
                except ValueError:
                    pass

    # ---------- 调度辅助 ----------
    def set_monitor_hook(self, fn) -> None:
        self._monitor_hook = fn

    def _interval_minutes(self, sub: dict) -> int:
        return int(sub.get("refresh_interval_minutes") or
                   self.config.get("default_refresh_minutes", 60))

    def stop(self) -> None:
        self._stop = True
        if self._thread:
            self._thread.join(timeout=5)

    @property
    def busy(self) -> bool:
        """是否有订阅正在刷新。"""
        return bool(self._running)

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run_forever, daemon=True,
                                        name="rss-scheduler")
        self._thread.start()

    def _run_forever(self) -> None:
        while not self._stop:
            try:
                now = time.time()
                for sub in self.subs.all(include_disabled=False):
                    last = self._last_refresh.get(sub["id"], 0)
                    if now - last >= self._interval_minutes(sub) * 60:
                        self.refresh_subscription(sub)
                if self._monitor_hook:
                    try:
                        self._monitor_hook()
                    except Exception as e:  # 监控失败不影响订阅
                        log.error("监控检查失败: %s", e)
            except Exception as e:
                log.error("调度循环异常: %s", e)
            time.sleep(60)

    # ---------- 订阅刷新 ----------
    def refresh_subscription(self, sub: dict) -> dict:
        sid = sub["id"]
        if sub.get("id") == "ugc_import":  # 一次性合集导入合成订阅，不参与定时刷新
            return {"new": 0, "error": "", "name": sub["name"]}
        with self._lock:
            if sid in self._running:
                return {"new": 0, "error": "正在刷新中", "name": sub["name"]}
            self._running.add(sid)
        try:
            adapter = create_adapter(sub["adapter"], sub["config"], self.config.all())
            videos = adapter.fetch_videos()
            new = 0
            for v in videos:
                if self.storage.is_seen(sid, v.video_id):
                    continue
                self.storage.mark_seen(sid, v.video_id)
                # 合集订阅：分P 即目标，无需关键词匹配（关键词可空）
                if sub.get("adapter") == "ugc":
                    matched = [sub.get("name") or "合集"]
                else:
                    matched = match_keywords(v.title, sub["config"]["keywords"],
                                             sub["config"].get("match_logic", "any"))
                    # 关键词为空：跟踪该订阅最新 fetch_depth 条视频（去重由 seen 机制保证）
                    if not matched and not sub["config"].get("keywords"):
                        matched = [sub.get("name") or "全部"]
                if matched and not is_excluded(
                        v.title, sub["config"].get("exclude_keywords", [])):
                    vitem = {
                        "video_id": v.video_id,
                        "title": v.title,
                        "url": v.url,
                        "cover": v.cover,
                        "author": v.author,
                        "published_at": v.published_at,
                    }
                    if self.storage.add_item(sid, vitem, matched):
                        new += 1
            self._last_refresh[sid] = time.time()
            self.storage.set_meta(f"last_refresh:{sid}", str(self._last_refresh[sid]))
            # 把 UP 昵称写回订阅 config（表格 ID 列直接显示昵称）
            if videos and not sub["config"].get("up_name"):
                sub["config"]["up_name"] = videos[0].author
                self.subs.save()
            return {"new": new, "error": "", "name": sub["name"]}
        except Exception as e:
            log.error("刷新订阅 %s 失败: %s", sub.get("name"), e)
            return {"new": 0, "error": str(e), "name": sub["name"]}
        finally:
            with self._lock:
                self._running.discard(sid)

    def refresh_all(self) -> dict[str, dict]:
        results: dict[str, dict] = {}
        for sub in self.subs.all(include_disabled=False):
            results[sub["id"]] = self.refresh_subscription(sub)
        return results

    def refresh_in_background(self, target: str | None = None,
                              on_done=None) -> None:
        """后台线程执行刷新，不阻塞 API 请求。"""

        def _job():
            if target is None:
                result = self.refresh_all()
            else:
                sub = self.subs.get(target)
                result = {target: self.refresh_subscription(sub)} if sub else {}
            if on_done:
                try:
                    on_done(result)
                except Exception as e:
                    log.error("刷新回调异常: %s", e)

        threading.Thread(target=_job, daemon=True, name="rss-refresh").start()
