"""M2 冒烟：真实调用 B 站公开 API 验证 WBI 签名链路。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.adapters import create_adapter

UID = 946974  # 影视飓风（知名 UP，视频多）
adapter = create_adapter("bilibili", {"uid": UID, "fetch_depth": 5}, {"cookie": ""})
videos = adapter.fetch_videos()
print(f"抓到 {len(videos)} 个视频:")
for v in videos[:5]:
    print(f"  - {v.title} | {v.url} | {v.author} | {v.published_at}")
assert len(videos) > 0, "未抓到视频"
assert all(v.video_id.startswith("BV") for v in videos), "video_id 非 bvid"
assert all(v.url.startswith("https://www.bilibili.com/video/") for v in videos)

# UID 解析
uid1 = adapter.resolve_uid("https://space.bilibili.com/946974/video")
uid2 = adapter.resolve_uid("946974")
print(f"解析: space链接={uid1}, 纯数字={uid2}")
assert uid1 == 946974 and uid2 == 946974
print("SMOKE OK")
