"""全局配置读写（存于 app.db 的 meta 表，不再生成 config.json）。

版本迁移：旧版 data/config.json 首次启动时一次性导入 meta 表后删除；
此后配置只存在于 app.db，避免安装目录/便携场景下文件权限问题。
"""
from __future__ import annotations

import json
from pathlib import Path

DEFAULTS: dict = {
    "port": 8848,
    "cookie": "",
    "default_refresh_minutes": 60,
    "default_scraper": "playwright",   # requests | playwright（默认 playwright，走本地 Edge/Chrome）
    "download_dir": "data/downloads",
}

# meta 表中的配置键
_META_KEY = "config"


class Config:
    def __init__(self, data_dir: str | Path = "data", storage=None):
        self.data_dir = Path(data_dir)
        self._owns_storage = storage is None
        if storage is None:
            # 兼容旧调用：Config(td) 独立建连（同库多连接 SQLite 允许）
            from core.storage import Storage
            storage = Storage(data_dir)
        self._storage = storage
        self._data: dict = dict(DEFAULTS)
        self._load()

    def close(self) -> None:
        """释放自建连接（复用外部 storage 时由外部负责关闭）。"""
        if self._owns_storage:
            try:
                self._storage.close()
            except Exception:
                pass
            self._owns_storage = False

    # ---------- 加载：meta 表优先，旧 config.json 一次性迁移 ----------
    def _load(self) -> None:
        raw = self._storage.get_meta(_META_KEY, "")
        if raw:
            try:
                merged = json.loads(raw)
            except json.JSONDecodeError:
                merged = {}
            for k, v in DEFAULTS.items():
                merged.setdefault(k, v)
            self._data = merged
            return

        # 旧版 config.json：一次性迁移进 meta 表后删除
        legacy = self.data_dir / "config.json"
        if legacy.exists():
            try:
                with open(legacy, "r", encoding="utf-8") as f:
                    merged = json.load(f)
                for k, v in DEFAULTS.items():
                    merged.setdefault(k, v)
                self._data = merged
                self.save()
                try:
                    legacy.unlink()  # 迁移成功后删除旧文件
                except OSError:
                    pass
                return
            except (json.JSONDecodeError, OSError):
                self._data = dict(DEFAULTS)
                return
        self._data = dict(DEFAULTS)

    # ---------- 持久化：写入 meta 表 ----------
    def save(self) -> None:
        self._storage.set_meta(_META_KEY, json.dumps(self._data, ensure_ascii=False))

    # ---------- 与旧版完全一致的读写接口 ----------
    def get(self, key: str, default=None):
        return self._data.get(key, default)

    def set(self, key: str, value) -> None:
        self._data[key] = value

    def all(self) -> dict:
        return dict(self._data)
