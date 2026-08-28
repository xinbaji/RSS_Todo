"""B 站合集订阅适配器：监控分P合集的更新，新分P 自动入清单。

订阅 config 存 bvid（用户输入的视频/合集链接），刷新时调 view 接口拿最新 pages，
每个分P 生成一个 VideoItem（video_id=bvid_pN），上层按 id 去重：
- 合集更新到新集（如 08→09）→ 新 video_id 自动入清单
- 已有分P 不重复入
"""
from __future__ import annotations

from ..bilibili_ugc import parse_collection
from .base import BaseAdapter, VideoItem


class BilibiliUgcAdapter(BaseAdapter):
    name = "ugc"

    def __init__(self, config=None, global_config=None):
        super().__init__(config, global_config)
        self.bvid = str(config.get("bvid", "") or "")
        self.cookie = (global_config or {}).get("cookie", "") or ""

    def fetch_videos(self) -> list[VideoItem]:
        if not self.bvid:
            raise ValueError("合集订阅缺少 bvid")
        info = parse_collection(f"https://www.bilibili.com/video/{self.bvid}",
                                cookie=self.cookie)
        out = []
        for p in info["pages"]:
            out.append(VideoItem(
                video_id=f"{info['bvid']}_p{p['page']}",
                title=f"{info['title']} - P{p['page']} {p['part']}".strip(),
                url=f"https://www.bilibili.com/video/{info['bvid']}?p={p['page']}",
                cover=info["pic"],
                author=info["up_name"],
                published_at=int(info.get("pubdate", 0) or 0),
                extra={"cid": p.get("cid", 0)},
            ))
        return out
