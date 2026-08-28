"""搜索模式端到端实测：真实 B 站 API + 完整链路（订阅→搜索→匹配→排除→入库）。"""
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import create_app  # noqa: E402

td = tempfile.mkdtemp(prefix="rsstodo_sm_")
app = None
try:
    app = create_app(Path(td))
    ctx = app.extensions["ctx"]

    # 1) 适配器层：搜索抓取
    from core.adapters import create_adapter
    a = create_adapter("bilibili", {
        "uid": 224267770,
        "keywords": [{"text": "无职转生"}, {"text": "中文字幕"}],
        "exclude_keywords": [{"text": "先行图"}],
        "fetch_depth": 10,
        "match_logic": "all",
        "fetch_mode": "latest",
    }, {"cookie": ""})
    vs = a.fetch_videos()
    print(f"[适配器] 搜索\"无职转生\" 抓到 {len(vs)} 条")
    for v in vs[:5]:
        print(f"    {v.title[:40]}")

    # 2) 完整链路：新增订阅（搜索模式）→ 刷新 → 入库
    r = ctx.subs.add({
        "name": "无职转生追踪",
        "config": {
            "uid": 224267770,
            "keywords": [{"text": "无职转生"}, {"text": "中文字幕"}],
            "exclude_keywords": [{"text": "先行图"}, {"text": "预告"}],
            "fetch_depth": 30,
        },
    })
    print(f"[订阅] 新增: {r['name']} (id={r['id']}) match_logic={r['config']['match_logic']}")
    result = ctx.scheduler.refresh_subscription(r)
    print(f"[刷新] 结果: new={result['new']} error={result['error']!r}")
    items = ctx.storage.list_items("todo")
    print(f"[入库] 待办清单 {len(items)} 条:")
    for it in items[:8]:
        print(f"    - {it['title'][:42]} | 命中: {it['matched_keywords']}")

    # 3) 排除词验证：确认没有"先行图/预告"标题的条目
    bad = [it for it in items if "先行图" in it["title"] or "预告" in it["title"]]
    print(f"[排除] 含排除词的条目数: {len(bad)}（应为 0）")
    assert not bad, "排除关键词未生效!"

    # 4) 去重验证：再刷一次不应新增
    r2 = ctx.scheduler.refresh_subscription(r)
    print(f"[去重] 二次刷新 new={r2['new']}（应为 0）")
    assert r2["new"] == 0, "去重失效"

    print("\n搜索模式端到端 SMOKE OK")
finally:
    if app is not None:
        try:
            app.extensions["ctx"].shutdown()
        except Exception:
            pass
    shutil.rmtree(td, ignore_errors=True)
