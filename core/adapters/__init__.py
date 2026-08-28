"""适配器注册表。新增站点：实现 BaseAdapter 并在此注册。"""
from __future__ import annotations

from .base import BaseAdapter, VideoItem
from .bilibili import BilibiliAdapter, BilibiliError
from .ugc import BilibiliUgcAdapter

ADAPTERS: dict[str, type[BaseAdapter]] = {
    BilibiliAdapter.name: BilibiliAdapter,
    BilibiliUgcAdapter.name: BilibiliUgcAdapter,
}


def create_adapter(adapter_name: str, config: dict | None = None,
                   global_config: dict | None = None) -> BaseAdapter:
    cls = ADAPTERS.get(adapter_name)
    if cls is None:
        raise ValueError(f"不支持的适配器: {adapter_name}")
    return cls(config, global_config)


__all__ = ["BaseAdapter", "VideoItem", "BilibiliAdapter", "BilibiliError",
           "ADAPTERS", "create_adapter"]
