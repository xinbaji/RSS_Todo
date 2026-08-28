"""core 层最小单元测试（matcher / rules / storage）。"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.matcher import match_keywords  # noqa: E402
from core.rules import Subscriptions, normalize_subscription, parse_uid_from_url  # noqa: E402
from core.storage import Storage  # noqa: E402

passed = 0
failed = 0


def check(name, cond):
    global passed, failed
    if cond:
        passed += 1
        print(f"  ok  {name}")
    else:
        failed += 1
        print(f"FAIL  {name}")


def _expect_error(fn):
    try:
        fn()
        return False
    except (ValueError, KeyError):
        return True


# ---------- matcher ----------
print("== matcher ==")
check("any: 子串命中", match_keywords("Python 爬虫教程", [{"text": "爬虫"}], "any") == ["爬虫"])
check("any: 未命中", match_keywords("Python 教程", [{"text": "爬虫"}], "any") == [])
check("any: 多关键词任一", match_keywords("评测视频", [
    {"text": "教程"}, {"text": "评测"}], "any") == ["评测"])
check("all: 全部命中", match_keywords("Python 爬虫教程", [
    {"text": "Python"}, {"text": "爬虫"}], "all") == ["Python", "爬虫"])
check("all: 缺一不命中", match_keywords("Python 教程", [
    {"text": "Python"}, {"text": "爬虫"}], "all") == [])
check("大小写不敏感默认", match_keywords("Learn PYTHON", [{"text": "python"}]) == ["python"])
check("大小写敏感", match_keywords("Learn PYTHON", [
    {"text": "python", "case_sensitive": True}]) == [])
check("正则命中", match_keywords("v1.2.3 发布", [
    {"text": r"v\d+\.\d+\.\d+", "regex": True}]) == ["v\\d+\\.\\d+\\.\\d+"])
check("非法正则降级子串", match_keywords("a(b 标题", [
    {"text": "a(b", "regex": True}]) == ["a(b"])
check("空关键词", match_keywords("标题", []) == [])

# ---------- rules ----------
print("== rules ==")
check("normalize 默认 all", normalize_subscription({
    "name": "测试UP", "config": {"uid": 123, "keywords": ["爬虫"]}})["config"]["match_logic"] == "all")
check("normalize 缺 uid 报错", _expect_error(
    lambda: normalize_subscription({"name": "x", "config": {"keywords": ["k"]}})))
check("normalize 空关键词允许(全量跟踪)", normalize_subscription(
    {"name": "x", "config": {"uid": 1}})["config"]["keywords"] == [])
check("关键词字符串归一化", normalize_subscription({
    "name": "x", "config": {"uid": 1, "keywords": ["A", {"text": "B"}]}}
)["config"]["keywords"] == [{"text": "A", "regex": False, "case_sensitive": False},
                            {"text": "B", "regex": False, "case_sensitive": False}])
check("parse uid 空间链接", parse_uid_from_url("https://space.bilibili.com/123456789/video") == 123456789)
check("parse uid 纯数字", parse_uid_from_url("123456789") == 123456789)
check("parse uid 非法", parse_uid_from_url("https://bilibili.com/") is None)

with tempfile.TemporaryDirectory() as td:
    subs = Subscriptions(td)
    sub = subs.add({"name": "UP1", "config": {"uid": 111, "keywords": ["教程"], "match_logic": "all"}})
    check("订阅新增", sub["id"] and sub["enabled"] is True)
    check("订阅持久化", Subscriptions(td).get(sub["id"]) is not None)
    subs.update(sub["id"], {"name": "UP2", "config": {"uid": 111, "keywords": ["教程"]}})
    check("订阅更新", Subscriptions(td).get(sub["id"])["name"] == "UP2")
    check("订阅删除", subs.remove(sub["id"]) and subs.get(sub["id"]) is None)
    check("删除不存在", subs.remove("none") is False)

# ---------- storage ----------
print("== storage ==")
with tempfile.TemporaryDirectory() as td:
    st = Storage(td)
    video = {"video_id": "BV1xx", "title": "测试视频", "url": "https://b23.tv/x",
             "cover": "", "author": "UP", "published_at": 1700000000}
    check("新增条目", st.add_item("s1", video, ["爬虫"]) is True)
    check("重复不新增", st.add_item("s1", video, ["爬虫"]) is False)
    check("seen 去重", st.is_seen("s1", "BV1xx") is False)
    st.mark_seen("s1", "BV1xx")
    check("seen 已记录", st.is_seen("s1", "BV1xx") is True)
    check("stats todo=1", st.item_stats()["todo"] == 1)
    items = st.list_items()
    check("列表含关键词", items[0]["matched_keywords"] == ["爬虫"])
    check("状态切换", st.set_status(items[0]["id"], "done") is True)
    check("stats done=1", st.item_stats()["done"] == 1)
    check("非法状态拒绝", st.set_status(items[0]["id"], "bad") is False)
    st.reset_history("s1")
    check("重置历史", st.is_seen("s1", "BV1xx") is False)
    # downloads
    dl = st.add_download(items[0], "video", "best", "data/downloads")
    check("下载任务创建", dl > 0)
    st.update_download(dl, status="running", progress=45.0)
    check("下载进度更新", st.get_download(dl)["progress"] == 45.0)
    check("下载列表", len(st.list_downloads()) == 1)
    # monitor
    st.set_monitor_value("m1", "¥3,299", 1700000000)
    check("监控值存储", st.monitor_values()["m1"]["value"] == "¥3,299")
    st.close()

print(f"\n结果: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
