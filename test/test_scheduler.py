"""Scheduler 并发与状态专项测试。

覆盖：防重入 / seen_videos 去重 / 刷新间隔回落 / refresh_all 过滤禁用 /
      refresh_in_background 非阻塞回调。仅 monkeypatch core.scheduler.create_adapter。
"""
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.config import Config  # noqa: E402
from core.rules import Subscriptions  # noqa: E402
from core.scheduler import Scheduler  # noqa: E402
from core.storage import Storage  # noqa: E402
from core.adapters.base import VideoItem  # noqa: E402
import core.scheduler as scheduler_mod  # noqa: E402

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


# ---------- 假适配器：可慢速、可计数 ----------
class FakeAdapter:
    name = "bilibili"
    calls = 0
    delay = 0.0
    _lock = threading.Lock()

    def __init__(self, *a, **k):
        pass

    def fetch_videos(self):
        with FakeAdapter._lock:
            FakeAdapter.calls += 1
        if FakeAdapter.delay:
            time.sleep(FakeAdapter.delay)
        return [
            VideoItem("BVdemo0001", "Python 爬虫实战视频教程",
                      "https://www.bilibili.com/video/BVdemo0001",
                      author="测试UP", published_at=int(time.time()) - 3600),
        ]


def _setup(td):
    storage = Storage(td)
    subs = Subscriptions(td, storage=storage)
    cfg = Config(td, storage=storage)
    return Scheduler(storage, subs, cfg)


def _teardown(sched):
    """关闭 scheduler 持有的所有 SQLite 连接（Windows 文件锁）。"""
    if hasattr(sched, "subs") and hasattr(sched.subs, "close"):
        sched.subs.close()
    if hasattr(sched, "storage") and hasattr(sched.storage, "close"):
        sched.storage.close()


# ---------- 1. 防重入：同订阅双线程并发 ----------
print("== 1. 防重入（慢适配器 1.5s） ==")
FakeAdapter.calls = 0
FakeAdapter.delay = 1.5
scheduler_mod.create_adapter = lambda *a, **k: FakeAdapter()
with tempfile.TemporaryDirectory() as td:
    sched = _setup(td)
    sub = sched.subs.add({"name": "UP1", "config": {"uid": 111, "keywords": ["爬虫"]}})
    barrier = threading.Barrier(2)
    results = []

    def call():
        barrier.wait()
        results.append(sched.refresh_subscription(sub))

    ts = [threading.Thread(target=call) for _ in range(2)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()

    check("双线程同订阅: fetch_videos 仅执行 1 次", FakeAdapter.calls == 1)
    check("双线程同订阅: 入清单 1 条", len(sched.storage.list_items()) == 1)
    check("双线程同订阅: 成功结果 new=1",
          any(r.get("new") == 1 for r in results))
    check("双线程同订阅: 另一线程被拦截",
          any(r.get("error") == "正在刷新中" for r in results))
    check("双线程同订阅: _running 已清空", sched._running == set())
    _teardown(sched)

# ---------- 2. 去重：连续刷新两次 ----------
print("== 2. 去重（seen_videos） ==")
FakeAdapter.calls = 0
FakeAdapter.delay = 0
with tempfile.TemporaryDirectory() as td:
    sched = _setup(td)
    sub = sched.subs.add({"name": "UP2", "config": {"uid": 222, "keywords": ["爬虫"]}})
    r1 = sched.refresh_subscription(sub)
    n1 = len(sched.storage.list_items())
    r2 = sched.refresh_subscription(sub)
    n2 = len(sched.storage.list_items())
    check("首次刷新: 新增 1 条", r1["new"] == 1 and n1 == 1)
    check("二次刷新: 不新增条目", r2["new"] == 0 and n2 == 1)
    check("二次刷新: 视频已标记 seen",
          sched.storage.is_seen(sub["id"], "BVdemo0001"))
    _teardown(sched)

# ---------- 3. 刷新间隔：缺省回落 / 显式生效 ----------
print("== 3. 刷新间隔 ==")
with tempfile.TemporaryDirectory() as td:
    sched = _setup(td)
    sched.config.set("default_refresh_minutes", 30)
    sub_def = sched.subs.add({"name": "缺省", "config": {"uid": 333, "keywords": ["k"]}})
    check("缺省回落全局默认 30", sched._interval_minutes(sub_def) == 30)
    sub_exp = sched.subs.add({
        "name": "显式", "refresh_interval_minutes": 120,
        "config": {"uid": 334, "keywords": ["k"]}})
    check("显式配置用订阅值 120", sched._interval_minutes(sub_exp) == 120)
    _teardown(sched)

# ---------- 4. refresh_all 只处理 enabled ----------
print("== 4. refresh_all 过滤禁用订阅 ==")
FakeAdapter.calls = 0
FakeAdapter.delay = 0
with tempfile.TemporaryDirectory() as td:
    sched = _setup(td)
    on_sub = sched.subs.add({"name": "开", "config": {"uid": 441, "keywords": ["爬虫"]}})
    off_sub = sched.subs.add({
        "name": "关", "enabled": False, "config": {"uid": 442, "keywords": ["爬虫"]}})
    res = sched.refresh_all()
    check("结果只含启用订阅", set(res.keys()) == {on_sub["id"]})
    check("禁用订阅未触发抓取", FakeAdapter.calls == 1)
    check("清单只 1 条（启用订阅）", len(sched.storage.list_items()) == 1)
    check("禁用订阅 id 不在结果", off_sub["id"] not in res)
    _teardown(sched)

# ---------- 5. refresh_in_background 非阻塞 + 回调 ----------
print("== 5. refresh_in_background ==")
FakeAdapter.calls = 0
FakeAdapter.delay = 0.8
with tempfile.TemporaryDirectory() as td:
    sched = _setup(td)
    sub = sched.subs.add({"name": "UP5", "config": {"uid": 555, "keywords": ["爬虫"]}})
    got, done = {}, threading.Event()

    def on_done(result):
        got.update(result)
        done.set()

    t0 = time.time()
    sched.refresh_in_background(sub["id"], on_done=on_done)
    elapsed = time.time() - t0
    check("调用秒级返回（未阻塞 0.8s 抓取）", elapsed < 0.5)
    ok = done.wait(timeout=5)
    check("回调已触发", ok)
    check("回调收到结果 dict",
          isinstance(got, dict) and sub["id"] in got and got[sub["id"]]["new"] == 1)
    check("后台刷新已入清单", len(sched.storage.list_items()) == 1)
    _teardown(sched)

print(f"\n结果: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
