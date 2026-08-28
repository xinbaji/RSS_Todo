"""API 层边界用例：复用 test_api.py 的 FakeBilibiliAdapter + monkeypatch 方式。

覆盖：订阅 / 清单 / 监控 / 下载 / 配置 的边界状态码与生命周期。
不修改 web/server.py，发现状态码不符合预期时仅记录并报告。
"""
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import create_app  # noqa: E402
from core.adapters.base import VideoItem  # noqa: E402
import core.scheduler as scheduler_mod  # noqa: E402
import core.downloader as downloader_mod  # noqa: E402


class FakeBilibiliAdapter:
    name = "bilibili"

    def __init__(self, *a, **k):
        pass

    def fetch_videos(self):
        return [
            VideoItem("BV1test001", "Python 爬虫实战视频教程",
                      "https://www.bilibili.com/video/BV1test001",
                      cover="", author="影视飓风", published_at=int(time.time()) - 3600),
        ]


scheduler_mod.create_adapter = lambda *a, **k: FakeBilibiliAdapter()

# 禁用下载 worker 线程：只验证 API 层的任务状态流转（pending -> canceled -> pending），
# 不启动真实下载线程，避免 resume 后后台线程在 app shutdown 后写已关闭 DB 的竞态崩溃。
downloader_mod.DownloadManager._ensure_worker = lambda self: None

td = tempfile.mkdtemp(prefix="rsstodo_extra_")
app = None
passed = 0
bugs = []


def ok():
    global passed
    passed += 1


def wait_status(client, dl_id, want, tries=40, dt=0.25):
    """轮询下载任务直到状态达到 want，返回任务 dict 或 None。"""
    for _ in range(tries):
        dls = client.get("/api/downloads").get_json()["downloads"]
        dl = next((d for d in dls if d["id"] == dl_id), None)
        if dl and dl["status"] == want:
            return dl
        time.sleep(dt)
    return dl


try:
    app = create_app(Path(td))
    client = app.test_client()

    # ---------- a. 订阅 ----------
    # PUT 更新不存在的订阅 -> 400
    r = client.put("/api/subscriptions/does-not-exist",
                   json={"name": "x", "config": {"uid": 1, "keywords": ["a"]}})
    assert r.status_code == 400, f"PUT 不存在订阅应 400: {r.status_code} {r.get_json()}"
    ok()

    # DELETE 不存在的订阅 -> 404
    r = client.delete("/api/subscriptions/does-not-exist")
    assert r.status_code == 404, f"DELETE 不存在订阅应 404: {r.status_code}"
    ok()

    # 新增 keywords 为空 -> 允许（全量跟踪最新 fetch_depth 条）
    r = client.post("/api/subscriptions",
                    json={"name": "x", "config": {"uid": 1, "keywords": []}})
    assert r.status_code == 200, f"空 keywords 应 200: {r.status_code} {r.get_json()}"
    ok()

    # parse-uid 非法 URL -> 400
    r = client.post("/api/subscriptions/parse-uid", json={"url": "abc"})
    assert r.status_code == 400, f"非法 URL 应 400: {r.status_code} {r.get_json()}"
    ok()

    # parse-uid 合法 space 链接 -> 200 且 uid 正确
    r = client.post("/api/subscriptions/parse-uid",
                    json={"url": "space.bilibili.com/123456789"})
    assert r.status_code == 200, r.get_json()
    assert r.get_json()["uid"] == 123456789, r.get_json()
    ok()

    # 建一条真实订阅并刷新，供清单 / 下载用例使用
    r = client.post("/api/subscriptions", json={
        "name": "影视飓风", "config": {"uid": 946974, "keywords": ["视频"], "match_logic": "any"}})
    assert r.status_code == 200, r.get_json()
    sid = r.get_json()["subscription"]["id"]
    client.post(f"/api/refresh/{sid}")
    items = []
    for _ in range(20):
        items = client.get("/api/items?status=all").get_json()["items"]
        if items:
            break
        time.sleep(0.5)
    assert items, "刷新后应有清单条目"
    it = items[0]

    # ---------- b. 清单 ----------
    # PATCH 不存在的 item -> 400
    r = client.patch("/api/items/999999", json={"status": "done"})
    assert r.status_code == 400, f"PATCH 不存在 item 应 400: {r.status_code} {r.get_json()}"
    ok()

    # PATCH 非法 status -> 400
    r = client.patch(f"/api/items/{it['id']}", json={"status": "invalid_status"})
    assert r.status_code == 400, f"非法 status 应 400: {r.status_code} {r.get_json()}"
    ok()

    # ---------- c. 监控 ----------
    # 新增缺 URL -> 400
    r = client.post("/api/monitor/rules", json={"name": "x", "config": {"xpath": "//h1"}})
    assert r.status_code == 400, f"缺 URL 应 400: {r.status_code} {r.get_json()}"
    ok()

    # 刷新不存在的 rule：按实际返回断言（期望语义为 404/400，实测 200+ok:False）
    r = client.post("/api/monitor/refresh/does-not-exist")
    body = r.get_json()
    if r.status_code == 200 and body.get("ok") is False:
        bugs.append("web/server.py:235 monitor_refresh_one 刷新不存在规则返回 200+ok:false，"
                    "不符合 404/400 语义")
    else:
        assert r.status_code in (400, 404), f"刷新不存在规则应 400/404: {r.status_code} {body}"
    ok()

    # 错误 XPath：刷新接口 200，但 last_error 非空
    r = client.post("/api/monitor/rules", json={
        "name": "错误XPath", "config": {"url": "https://example.com", "xpath": "//不存在的节点"}})
    assert r.status_code == 200, r.get_json()
    rid = r.get_json()["rule"]["id"]
    r = client.post(f"/api/monitor/refresh/{rid}")
    assert r.status_code == 200, f"错误 XPath 刷新接口应 200: {r.status_code} {r.get_json()}"
    rules = client.get("/api/monitor/rules").get_json()["rules"]
    hit = next((x for x in rules if x["id"] == rid), None)
    assert hit and hit["last_error"], f"错误 XPath 后 last_error 应非空: {hit}"
    ok()

    # ---------- d. 下载 ----------
    # POST 下载不存在的 item -> 404
    r = client.post("/api/items/999999/download", json={"content_type": "video"})
    assert r.status_code == 404, f"下载不存在 item 应 404: {r.status_code} {r.get_json()}"
    ok()

    # danmaku 任务：cancel -> canceled；resume -> pending 重新入队
    r = client.post(f"/api/items/{it['id']}/download",
                    json={"content_type": "danmaku", "quality": "best",
                          "save_dir": str(Path(td) / "dl")})
    assert r.status_code == 200, r.get_json()
    dl_id = r.get_json()["download_id"]
    r = client.post(f"/api/downloads/{dl_id}/cancel")
    assert r.status_code == 200, f"cancel 应 200: {r.status_code} {r.get_json()}"
    dl = wait_status(client, dl_id, "canceled")
    assert dl and dl["status"] == "canceled", f"cancel 后应为 canceled: {dl}"
    ok()

    time.sleep(0.3)  # 等 worker 清空队列退出，降低 resume 竞态
    r = client.post(f"/api/downloads/{dl_id}/resume")
    assert r.status_code == 200, f"resume 应 200: {r.status_code} {r.get_json()}"
    dl = next(d for d in client.get("/api/downloads").get_json()["downloads"] if d["id"] == dl_id)
    assert dl["status"] == "pending", f"resume 后应为 pending 重新入队: {dl}"
    ok()
    # 收敛：立即取消，并等待任务离开 running/pending，
    # 确保 worker 线程在 app shutdown 前结束（避免写已关闭 DB 的后台崩溃）
    client.post(f"/api/downloads/{dl_id}/cancel")
    for _ in range(40):
        dl = next((d for d in client.get("/api/downloads").get_json()["downloads"]
                   if d["id"] == dl_id), None)
        if dl and dl["status"] not in ("pending", "running"):
            break
        time.sleep(0.25)
    client.delete(f"/api/downloads/{dl_id}")

    # ---------- e. 配置：部分更新不影响其他字段 ----------
    r = client.put("/api/config", json={"download_dir": "d:/tmp/rss-extra", "port": 9999})
    assert r.status_code == 200, r.get_json()
    r = client.put("/api/config", json={"port": 8888})
    assert r.status_code == 200, r.get_json()
    cfg = client.get("/api/config").get_json()
    assert cfg["download_dir"] == "d:/tmp/rss-extra", f"部分更新不应覆盖其他字段: {cfg}"
    assert cfg["port"] == 8888, cfg
    ok()

    print(f"API EXTRA OK: {passed} cases")
    if bugs:
        print("发现的 bug:")
        for b in bugs:
            print(f"  - {b}")
finally:
    if app is not None:
        try:
            app.extensions["ctx"].shutdown()
        except Exception:
            pass
    shutil.rmtree(td, ignore_errors=True)
