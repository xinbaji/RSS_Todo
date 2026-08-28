"""core 层边界测试补充（matcher / rules / storage）。"""
import json
import sys
import tempfile
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

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


def _throws(fn):
    try:
        fn()
        return False
    except Exception:
        return True


# ---------- matcher 边界 ----------
print("== matcher 边界 ==")
check("空标题返回空", match_keywords("", [{"text": "爬虫"}], "any") == [])
check("全空格标题返回空", match_keywords("   ", [{"text": "爬虫"}], "any") == [])
check("中文正则命中", match_keywords("爬虫教程2024上线", [
    {"text": "教程\\d+", "regex": True}], "any") == ["教程\\d+"])
check("正则特殊字符转义", match_keywords("价格: $99.9", [
    {"text": r"\$\d+\.\d+", "regex": True}], "any") == [r"\$\d+\.\d+"])
check("keywords 含空 text 条目被跳过", match_keywords("Python 教程", [
    {"text": ""}, {"text": "   "}, {"text": "Python"}], "any") == ["Python"])
check("keywords 空条目不影响 all 判定", match_keywords("Python 爬虫", [
    {"text": ""}, {"text": "Python"}, {"text": "爬虫"}], "all") == ["Python", "爬虫"])
check("match_logic 非法值降级 any", match_keywords("Python 教程", [
    {"text": "Python"}, {"text": "爬虫"}], "weird") == ["Python"])

# ---------- rules 边界 ----------
print("== rules 边界 ==")
check("normalize keywords=None 抛异常", _throws(
    lambda: normalize_subscription({"name": "x", "config": {"uid": 1, "keywords": None}})))
_str_kw = normalize_subscription({"name": "x", "config": {"uid": 1, "keywords": "爬虫"}})
check("normalize keywords 传字符串不崩溃", _str_kw is not None)
check("interval 负数归 None", normalize_subscription({
    "name": "x", "config": {"uid": 1, "keywords": ["k"]},
    "refresh_interval_minutes": -5})["refresh_interval_minutes"] is None)
check("interval 0 归 None", normalize_subscription({
    "name": "x", "config": {"uid": 1, "keywords": ["k"]},
    "refresh_interval_minutes": 0})["refresh_interval_minutes"] is None)
check("uid 传字符串数字归一化", normalize_subscription({
    "name": "x", "config": {"uid": "123", "keywords": ["k"]}})["config"]["uid"] == 123)
check("fetch_depth=None 归默认 30", normalize_subscription({
    "name": "x", "config": {"uid": 1, "keywords": ["k"], "fetch_depth": None}}
)["config"]["fetch_depth"] == 30)
check("fetch_depth 超范围不崩溃", normalize_subscription({
    "name": "x", "config": {"uid": 1, "keywords": ["k"], "fetch_depth": 99999}}
)["config"]["fetch_depth"] == 99999)

with tempfile.TemporaryDirectory() as td:
    subs = Subscriptions(td)
    dup = {"id": "dup-1", "name": "UP", "config": {"uid": 1, "keywords": ["k"]}}
    check("相同 id 首次添加成功", subs.add(dup)["id"] == "dup-1")
    check("相同 id 重复添加报错", _expect_error(lambda: subs.add(dup)))

# ---------- storage 边界 ----------
print("== storage 边界 ==")
with tempfile.TemporaryDirectory() as td:
    st = Storage(td)
    video = {"video_id": "BVc0", "title": "并发视频", "url": "https://b23.tv/x",
             "cover": "", "author": "UP", "published_at": 1700000000}
    n = 10
    results = []
    barrier = threading.Barrier(n)

    def worker():
        barrier.wait()
        results.append(st.add_item("s1", video, ["并发"]))

    threads = [threading.Thread(target=worker) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    check("并发 10 线程同 video_id 仅一次入库", results.count(True) == 1)
    check("并发后 items 仅一条", len(st.list_items()) == 1)
    check("并发后 stats total=1", st.item_stats()["total"] == 1)
    check("set_status 不存在 id 返回 False", st.set_status(999999, "done") is False)
    st.set_monitor_value("m2", "v1", 100)
    st.set_monitor_value("m2", "v2", 200)
    mv = st.monitor_values()
    check("monitor_values 覆盖更新", mv["m2"]["value"] == "v2" and mv["m2"]["fetched_at"] == 200)
    check("monitor_values 覆盖后仅一条", len(mv) == 1)
    st.close()

print(f"\n结果: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
