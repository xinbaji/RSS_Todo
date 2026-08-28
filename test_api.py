"""API 集成冒烟：Flask test_client 全链路验证（含真实 B 站抓取与 example.com 监控）。"""
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app import create_app  # noqa: E402
from core.adapters.base import VideoItem  # noqa: E402

# 用假适配器替代真实 B 站抓取，保证链路测试稳定可回归；
# 真实 B 站抓取验证由 smoke_bilibili.py 独立承担（B 站无 Cookie 短时高频会被风控）
import core.scheduler as scheduler_mod  # noqa: E402


class FakeBilibiliAdapter:
    name = "bilibili"

    def __init__(self, *a, **k):
        pass

    def fetch_videos(self):
        return [
            VideoItem("BV1test001", "Python 爬虫实战视频教程", "https://www.bilibili.com/video/BV1test001",
                      cover="", author="影视飓风", published_at=int(time.time()) - 3600),
            VideoItem("BV1test002", "普通标题内容", "https://www.bilibili.com/video/BV1test002",
                      cover="", author="影视飓风", published_at=int(time.time()) - 7200),
        ]


scheduler_mod.create_adapter = lambda *a, **k: FakeBilibiliAdapter()

td = tempfile.mkdtemp(prefix="rsstodo_")
app = None
try:
    app = create_app(Path(td))
    client = app.test_client()

    # 页面
    r = client.get("/")
    assert r.status_code == 200 and b"rss-todo" in r.data, "首页失败"

    # 订阅：新增
    r = client.post("/api/subscriptions", json={
        "name": "影视飓风", "config": {"uid": 946974, "keywords": ["视频"], "match_logic": "any"}})
    assert r.status_code == 200, r.get_json()
    sid = r.get_json()["subscription"]["id"]

    # UID 解析
    r = client.post("/api/subscriptions/parse-uid", json={"url": "https://space.bilibili.com/946974"})
    assert r.status_code == 200 and r.get_json()["uid"] == 946974

    # 非法订阅
    r = client.post("/api/subscriptions", json={"name": "x", "config": {"keywords": []}})
    assert r.status_code == 400, "非法订阅应 400"

    # 手动刷新（后台线程；mock 适配器返回固定视频，命中"视频"关键词）
    r = client.get("/api/refresh/status")
    assert r.status_code == 200 and "running" in r.get_json(), "刷新状态接口异常"
    r = client.post(f"/api/refresh/{sid}")
    assert r.status_code == 200
    items = []
    for _ in range(20):
        items = client.get("/api/items?status=all").get_json()["items"]
        if items:
            break
        time.sleep(0.5)
    assert items, "刷新后应有清单条目"
    assert items[0]["video_id"] == "BV1test001", "未命中关键词的视频不应入清单"
    print(f"  ok mock 抓取入清单 {len(items)} 条: {items[0]['title']}")

    # 状态切换
    r = client.patch(f"/api/items/{items[0]['id']}", json={"status": "done"})
    assert r.status_code == 200
    stats = client.get("/api/items/stats").get_json()
    assert stats["done"] >= 1, "done 计数异常"

    # 监控：新增 + 刷新（example.com 静态页；显式 requests，避免依赖 playwright 环境）
    r = client.post("/api/monitor/rules", json={
        "name": "示例域", "scraper": "requests",
        "config": {"url": "https://example.com", "xpath": "//h1/text()"}})
    assert r.status_code == 200, r.get_json()
    rid = r.get_json()["rule"]["id"]
    r = client.post(f"/api/monitor/refresh/{rid}")
    assert r.status_code == 200
    rules = client.get("/api/monitor/rules").get_json()["rules"]
    hit = next((x for x in rules if x["id"] == rid), None)
    assert hit and hit["value"] == "Example Domain", f"监控值异常: {hit}"
    print(f"  ok 监控抓取成功: {hit['value']}")

    # 配置读写
    r = client.put("/api/config", json={"download_dir": "d:/tmp/rss-dl", "port": 9999})
    assert r.status_code == 200
    cfg = client.get("/api/config").get_json()
    assert cfg["download_dir"] == "d:/tmp/rss-dl", "配置未保存"

    # 下载：条目不存在 404；存在则创建任务
    r = client.post("/api/items/99999/download", json={"content_type": "video"})
    assert r.status_code == 404
    r = client.post(f"/api/items/{items[0]['id']}/download",
                    json={"content_type": "danmaku", "quality": "best",
                          "save_dir": str(Path(td) / "dl")})
    assert r.status_code == 200, r.get_json()
    dls = client.get("/api/downloads").get_json()["downloads"]
    assert len(dls) == 1 and dls[0]["status"] in ("pending", "running", "success", "failed", "canceled")
    client.post(f"/api/downloads/{dls[0]['id']}/cancel")
    r = client.delete(f"/api/downloads/{dls[0]['id']}")
    assert r.status_code == 200

    # 删除订阅
    r = client.delete(f"/api/subscriptions/{sid}")
    assert r.status_code == 200

    print("API SMOKE OK")
finally:
    if app is not None:
        try:
            app.extensions["ctx"].shutdown()
        except Exception:
            pass
    shutil.rmtree(td, ignore_errors=True)
