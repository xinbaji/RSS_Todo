"""monitor.py 专项验证：XPath 提取 / 真实网络 / playwright 错误路径 / MonitorRules CRUD。"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lxml import html  # noqa: E402
from core import monitor  # noqa: E402
from core.monitor import (  # noqa: E402
    MonitorRuleError, MonitorRules, normalize_rule, scrape_rule, _extract_value,
)

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
    except MonitorRuleError:
        return True


# ---------- 1. 本地 HTML：XPath 提取 ----------
print("== 本地 XPath 提取 ==")
HTML = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>测试页</title></head>
<body>
<h1>标题</h1>
<a href="https://x.com">链接</a>
<p>第一段内容</p>
<p>第二段内容</p>
<p>第三段内容</p>
</body></html>"""

with tempfile.TemporaryDirectory() as td:
    html_path = Path(td) / "sample.html"
    html_path.write_text(HTML, encoding="utf-8")
    doc = html.fromstring(html_path.read_bytes())

    v = _extract_value(doc.xpath("//h1/text()"))
    check("//h1/text() 提取文本", v == "标题")

    v = _extract_value(doc.xpath("//a/@href"))
    check("//a/@href 提取属性", v == "https://x.com")

    nodes = doc.xpath("//p")
    check("//p 匹配 3 个节点", len(nodes) == 3)
    v = _extract_value(nodes)
    check("//p 取第一个元素文本", v == "第一段内容")

    # 用与 _scrape_requests 完全相同的路径（解析 + xpath + _extract_value）
    v = _extract_value(doc.xpath("//body//p[2]/text()"))
    check("//p[2] 第二个段落", v == "第二段内容")

# ---------- 2. 真实网络 ----------
print("== 真实网络 example.com ==")
val, err = scrape_rule({"config": {"url": "https://example.com", "xpath": "//h1/text()"}})
check("example.com h1=Example Domain", val == "Example Domain" and err == "")

val, err = scrape_rule({"config": {"url": "https://example.com", "xpath": "//不存在的节点/text()"}})
check("不存在节点 value 空 + error 非空", val == "" and bool(err))

# ---------- 3. playwright 错误路径（未安装） ----------
print("== playwright 未安装错误路径 ==")
EXPECT_PW_ERR = "playwright 未安装：pip install playwright && playwright install chromium"
val, err = monitor._scrape_playwright("https://example.com", "//h1/text()")
check("直接调用 _scrape_playwright 友好错误", val == "" and err == EXPECT_PW_ERR)

val, err = scrape_rule({
    "config": {"url": "https://example.com", "xpath": "//h1/text()"},
    "scraper": "playwright",
})
check("scrape_rule(playwright) 友好错误", val == "" and err == EXPECT_PW_ERR)

# ---------- 4. normalize 与 MonitorRules CRUD ----------
print("== normalize_rule ==")
r = normalize_rule({
    "config": {"url": "https://example.com", "xpath": "//h1"},
    "scraper": "selenium",
    "tags": [{"t": "长" * 30, "c": ""}, {"t": "短标签", "c": "#ff0000"}, {}],
})
check("非法 scraper 降级 requests", r["scraper"] == "requests")
check("tags 长文本截断 20 字", r["tags"][0]["t"] == "长" * 20)
check("tags 缺颜色默认 #2f6fed", r["tags"][0]["c"] == "#2f6fed")
check("tags 保留自定义颜色", r["tags"][1]["c"] == "#ff0000")
check("tags 空 dict 丢弃", len(r["tags"]) == 2)
check("缺名默认 监控 1", r["name"] == "监控 1")

check("缺 URL 抛 MonitorRuleError", _expect_error(
    lambda: normalize_rule({"config": {"xpath": "//h1"}})))
check("非法 URL 抛 MonitorRuleError", _expect_error(
    lambda: normalize_rule({"config": {"url": "ftp://x", "xpath": "//h1"}})))
check("缺 XPath 抛 MonitorRuleError", _expect_error(
    lambda: normalize_rule({"config": {"url": "https://example.com"}})))

print("== MonitorRules CRUD ==")
with tempfile.TemporaryDirectory() as td:
    rules = MonitorRules(td)
    r = rules.add({"name": "监控A",
                   "config": {"url": "https://example.com", "xpath": "//h1/text()"}})
    check("新增返回 id", bool(r["id"]))
    check("get 命中", rules.get(r["id"]) is not None)
    check("持久化", MonitorRules(td).get(r["id"]) is not None)

    rules.update(r["id"], {"name": "监控B",
                           "config": {"url": "https://example.com", "xpath": "//title"}})
    check("更新生效", MonitorRules(td).get(r["id"])["name"] == "监控B")

    rules.add({"name": "停用", "enabled": False,
               "config": {"url": "https://example.com", "xpath": "//h1"}})
    check("all 含停用", len(rules.all()) == 2)
    check("all 过滤停用", len(rules.all(include_disabled=False)) == 1)

    check("删除", rules.remove(r["id"]) is True and rules.get(r["id"]) is None)
    check("删除不存在返回 False", rules.remove("none") is False)

    check("更新不存在抛错", _expect_error(
        lambda: rules.update("none", {"config": {"url": "https://x.com", "xpath": "//h1"}})))

print(f"\n结果: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
