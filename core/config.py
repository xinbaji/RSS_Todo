"""全局配置读写（data/config.json）。敏感信息（Cookie 等）独立于此文件。"""
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


class Config:
    def __init__(self, data_dir: str | Path = "data"):
        self.data_dir = Path(data_dir)
        self.path = self.data_dir / "config.json"
        self._data: dict = dict(DEFAULTS)
        self.load()

    def load(self) -> None:
        if self.path.exists():
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    merged = json.load(f)
                for k, v in DEFAULTS.items():
                    if k not in merged:
                        merged[k] = v
                self._data = merged
            except (json.JSONDecodeError, OSError):
                self._data = dict(DEFAULTS)
        else:
            self._data = dict(DEFAULTS)

    def save(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self._data, f, ensure_ascii=False, indent=2)
        tmp.replace(self.path)

    def get(self, key: str, default=None):
        return self._data.get(key, default)

    def set(self, key: str, value) -> None:
        self._data[key] = value

    def all(self) -> dict:
        return dict(self._data)
