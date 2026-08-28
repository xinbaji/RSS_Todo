"""适配器抽象接口与标准条目结构。未来新增站点只需实现 BaseAdapter 并注册。"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class VideoItem:
    video_id: str
    title: str
    url: str
    cover: str = ""
    author: str = ""
    published_at: int = 0
    extra: dict = field(default_factory=dict)


class BaseAdapter(ABC):
    """站点适配器基类。

    - name: 站点标识，对应订阅规则里的 "adapter" 字段
    - config: 该订阅的 config（如 uid / keywords 等站点专属参数）
    - global_config: 全局配置（提供 cookie 等跨站点能力）
    """

    name: str = "base"

    def __init__(self, config: dict | None = None, global_config: dict | None = None):
        self.config = config or {}
        self.global_config = global_config or {}

    @abstractmethod
    def fetch_videos(self) -> list[VideoItem]:
        """抓取订阅源最新视频列表（含历史，由上层做去重）。"""
        raise NotImplementedError
