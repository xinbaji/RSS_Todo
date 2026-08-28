"""监控引擎：URL + XPath 定时抓取网页数据。

- requests 方案: lxml 解析 XPath（轻量备选）
- playwright 方案: 默认引擎；走本地系统 Edge/Chrome 的 channel（无头模式），
  不下载 chromium；playwright 为可选依赖懒加载，未安装仅相关规则报错
- 只保存最新值（monitor_values upsert），无历史
"""
from __future__ import annotations

import json
import logging
import re
import time
import uuid
from pathlib import Path

import requests

log = logging.getLogger("rss-todo.monitor")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")


class MonitorRuleError(ValueError):
    pass


def _new_id() -> str:
    return uuid.uuid4().hex[:10]


def normalize_rule(raw: dict, idx: int = 0) -> dict:
    name = str(raw.get("name", "")).strip() or f"监控 {idx + 1}"
    url = str(raw.get("config", {}).get("url", "")).strip()
    xpath = str(raw.get("config", {}).get("xpath", "")).strip()
    scraper = raw.get("scraper", "requests")
    if scraper not in ("requests", "playwright"):
        scraper = "requests"
    interval = int(raw.get("refresh_interval_minutes", 0) or 0)
    tags = raw.get("tags", []) or []
    norm_tags = []
    for t in tags:
        if isinstance(t, dict) and t.get("t"):
            norm_tags.append({"t": str(t["t"])[:20], "c": str(t.get("c") or "#2f6fed")})
    cfg = {"type": raw.get("config", {}).get("type", "xpath"), "url": url, "xpath": xpath,
           "headers": raw.get("config", {}).get("headers", {}) or {}}
    # bilibili_stat 类型：要求 aid + fields（多选，兼容旧 field 单选），不要求 url/xpath
    if cfg["type"] == "bilibili_stat":
        try:
            cfg["aid"] = int(raw.get("config", {}).get("aid"))
        except (TypeError, ValueError):
            raise MonitorRuleError(f"[{idx}] 监控 {name} 缺少有效 aid")
        fields = raw.get("config", {}).get("fields") or []
        if not fields and raw.get("config", {}).get("field"):
            fields = [raw["config"]["field"]]
        valid_fields = {"view", "like", "coin", "favorite", "share",
                        "danmaku", "reply"}
        cfg["fields"] = [f for f in fields if f in valid_fields]
        if not cfg["fields"]:
            raise MonitorRuleError(f"[{idx}] 监控 {name} fields 非法")
        cfg["url"] = ""
        cfg["xpath"] = ""
    elif cfg["type"] == "up":
        # UP 主监控：mid + 监控项列表（live/follower/view/likes）
        try:
            cfg["mid"] = int(raw.get("config", {}).get("mid") or 0)
        except (TypeError, ValueError):
            raise MonitorRuleError(f"[{idx}] 监控 {name} 缺少有效 mid")
        if not cfg["mid"]:
            raise MonitorRuleError(f"[{idx}] 监控 {name} 缺少有效 mid")
        items = raw.get("config", {}).get("items") or []
        cfg["items"] = [i for i in items
                        if i in {"live", "following", "follower", "view", "likes"}]
        if not cfg["items"]:
            cfg["items"] = ["live"]  # 至少监控直播
        cfg["url"] = ""
        cfg["xpath"] = ""
    elif cfg["type"] == "github_repo":
        # GitHub 仓库监控：owner/repo + 指标列表（stars/forks/issues/watchers/downloads）
        cfg["owner"] = str(raw.get("config", {}).get("owner", "")).strip()
        cfg["repo"] = str(raw.get("config", {}).get("repo", "")).strip()
        if not cfg["owner"] or not cfg["repo"]:
            raise MonitorRuleError(f"[{idx}] 监控 {name} 缺少 owner/repo")
        fields = raw.get("config", {}).get("fields") or []
        valid = {"stars", "forks", "issues", "watchers", "downloads"}
        cfg["fields"] = [f for f in fields if f in valid]
        if not cfg["fields"]:
            raise MonitorRuleError(f"[{idx}] 监控 {name} fields 非法")
        cfg["url"] = ""
        cfg["xpath"] = ""
    else:
        if not url or not url.startswith(("http://", "https://")):
            raise MonitorRuleError(f"[{idx}] 监控 {name} 缺少有效 URL")
        if not xpath:
            raise MonitorRuleError(f"[{idx}] 监控 {name} 缺少 XPath")
        cfg["type"] = "xpath"
    return {
        "id": str(raw.get("id") or _new_id()),
        "name": name,
        "enabled": bool(raw.get("enabled", True)),
        "scraper": scraper,
        "refresh_interval_minutes": interval if interval > 0 else None,
        "tags": norm_tags,
        "config": cfg,
    }


class MonitorRules:
    """监控规则集合（monitor.json）。"""

    def __init__(self, data_dir: str | Path = "data"):
        self.data_dir = Path(data_dir)
        self.path = self.data_dir / "monitor.json"
        self._list: list[dict] = []
        self.load()

    def load(self) -> None:
        if self.path.exists():
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    data = __import__("json").load(f)
                self._list = [normalize_rule(r, i) for i, r in enumerate(data.get("rules", []))]
            except (MonitorRuleError, KeyError, TypeError, ValueError, OSError):
                self._list = []
        else:
            self._list = []

    def save(self) -> None:
        import json
        self.data_dir.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"version": 1, "rules": self._list}, f, ensure_ascii=False, indent=2)
        tmp.replace(self.path)

    def all(self, include_disabled: bool = True) -> list[dict]:
        if include_disabled:
            return [dict(r) for r in self._list]
        return [dict(r) for r in self._list if r.get("enabled", True)]

    def get(self, rule_id: str) -> dict | None:
        for r in self._list:
            if r["id"] == rule_id:
                return dict(r)
        return None

    def add(self, raw: dict) -> dict:
        rule = normalize_rule(raw, len(self._list))
        self._list.append(rule)
        self.save()
        return dict(rule)

    def update(self, rule_id: str, raw: dict) -> dict:
        idx = next((i for i, r in enumerate(self._list) if r["id"] == rule_id), None)
        if idx is None:
            raise MonitorRuleError(f"监控规则不存在: {rule_id}")
        raw = dict(raw)
        raw["id"] = rule_id
        rule = normalize_rule(raw, idx)
        self._list[idx] = rule
        self.save()
        return dict(rule)

    def remove(self, rule_id: str) -> bool:
        before = len(self._list)
        self._list = [r for r in self._list if r["id"] != rule_id]
        changed = len(self._list) != before
        if changed:
            self.save()
        return changed


def _extract_value(nodes) -> str:
    """从 XPath 结果提取文本：优先 text()/@attr 字符串，Element 取文本内容。"""
    if not nodes:
        return ""
    for n in nodes:
        if isinstance(n, str):
            v = n.strip()
        elif isinstance(n, (int, float)):
            v = str(n)
        else:
            try:
                v = (n.text_content() or "").strip()
            except AttributeError:
                try:
                    v = str(n).strip()
                except Exception:
                    continue
        if v:
            return v
    return ""


def scrape_rule(rule: dict, global_config: dict | None = None) -> tuple[str, str]:
    """抓取单条规则，返回 (value, error)。

    数据来源 type:
      - xpath（默认）：用 XPath + scraper（requests/playwright）抓网页
      - bilibili_stat：调 B 站 API（view/reply），返回 aid 对应字段
    """
    global_config = global_config or {}
    cfg = rule.get("config", {})
    rtype = cfg.get("type", "xpath")
    if rtype == "bilibili_stat":
        try:
            from core.bilibili_stats import view_stat, reply_count
            aid = int(cfg.get("aid") or 0)
            field = cfg.get("field") or "view"
            if not aid:
                return "", "bilibili_stat 缺少 aid"
            cookie = global_config.get("cookie", "") or ""
            if field == "reply":
                v, e = reply_count(aid, cookie=cookie)
            else:
                info, e = view_stat_by_aid(aid, cookie)
                if not info:
                    return "", e or "view 失败"
                v = str(info["stat"].get(field, ""))
            return (v, "") if not e else ("", e)
        except Exception as e:
            return "", f"bilibili_stat 抓取异常: {e}"
    scraper = rule.get("scraper") or global_config.get("default_scraper", "requests")
    url = cfg.get("url", "")
    xpath = cfg.get("xpath", "")
    if not url or not xpath:
        return "", "缺少 URL 或 XPath"
    try:
        if scraper == "playwright":
            return _scrape_playwright(url, xpath)
        return _scrape_requests(url, xpath, cfg.get("headers", {}))
    except Exception as e:
        return "", f"抓取失败：{e}"


def view_stat_by_aid(aid: int, cookie: str = ""):
    from core.bilibili_stats import view_stat
    return view_stat_by_aid_inner(aid, cookie)


def view_stat_by_aid_inner(aid: int, cookie: str):
    """view API 按 aid 取统计（video 统计用）。"""
    import requests as _rq
    from core.bilibili_stats import VIEW_URL, UA
    headers = {"User-Agent": UA, "Referer": "https://www.bilibili.com/"}
    if cookie:
        headers["Cookie"] = cookie
    resp = _rq.get(VIEW_URL, params={"aid": aid}, headers=headers, timeout=15)
    data = resp.json()
    if data.get("code") != 0:
        return None, f"view API code={data.get('code')}: {data.get('message')}"
    return {
        "title": data["data"].get("title"),
        "author": data["data"].get("owner", {}).get("name", ""),
        "pic": data["data"].get("pic", ""),
        "stat": dict(data["data"].get("stat") or {}),
    }, ""


def _scrape_requests(url: str, xpath: str, headers: dict) -> tuple[str, str]:
    hdrs = {"User-Agent": UA}
    hdrs.update(headers or {})
    resp = requests.get(url, headers=hdrs, timeout=15)
    resp.raise_for_status()
    from lxml import html
    doc = html.fromstring(resp.content)
    nodes = doc.xpath(xpath)
    value = _extract_value(nodes)
    if not value:
        return "", "未匹配到 XPath 内容（页面结构可能变化）"
    return value, ""


def _scrape_playwright(url: str, xpath: str) -> tuple[str, str]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return "", "playwright 未安装：pip install playwright（使用系统 Edge/Chrome，无需下载 chromium）"
    elem_xpath, attr = _split_pw_xpath(xpath)
    with sync_playwright() as p:
        browser = _launch_local_browser(p)
        if browser is None:
            return "", "未检测到系统浏览器：请安装 Microsoft Edge 或 Google Chrome 后重试"
        try:
            page = browser.new_page()
            page.goto(url, timeout=30000, wait_until="domcontentloaded")
            loc = page.locator(f"xpath={elem_xpath}").first
            loc.wait_for(state="attached", timeout=15000)
            if attr:
                value = (loc.get_attribute(attr) or "").strip()
            else:
                try:
                    value = loc.inner_text(timeout=5000).strip()
                except Exception:
                    value = (loc.get_attribute("textContent") or "").strip()
            if not value:
                return "", "未匹配到 XPath 内容（页面结构可能变化）"
            return value, ""
        finally:
            try:
                browser.close()  # 关闭失败不影响抓取结果
            except Exception:
                pass


def _split_pw_xpath(xpath: str) -> tuple[str, str | None]:
    """playwright locator 只定位元素：拆掉结尾的 /text() 或 /@attr，返回 (元素xpath, 属性名|None)。"""
    x = (xpath or "").strip()
    if x.endswith("/text()"):
        return x[: -len("/text()")] or "/", None
    m = re.search(r"/@([\w-]+)$", x)
    if m:
        return x[: m.start()] or "/", m.group(1)
    return x or "/", None


def _launch_local_browser(p):
    """优先用系统已安装的 Edge/Chrome（无头模式），绝不下载 chromium。"""
    for channel in ("msedge", "chrome"):
        try:
            return p.chromium.launch(headless=True, channel=channel)
        except Exception:
            continue
    return None


class MonitorChecker:
    """调度器挂载的监控检查器：到期规则抓取并 upsert 最新值。"""

    def __init__(self, rules: MonitorRules, storage, config):
        self.rules = rules
        self.storage = storage
        self.config = config
        self._last: dict[str, float] = {}
        self._running: set[str] = set()
        self._load_persisted_state()

    def _load_persisted_state(self) -> None:
        for rule in self.rules.all():
            v = self.storage.get_meta(f"last_monitor:{rule['id']}")
            if v:
                try:
                    self._last[rule["id"]] = float(v)
                except ValueError:
                    pass

    def _save_last(self, rule_id: str) -> None:
        self.storage.set_meta(f"last_monitor:{rule_id}", str(self._last[rule_id]))

    def _check_up_rule(self, rule: dict) -> tuple[dict, int]:
        """UP 主监控：拉状态 → 比对上次 → 触发项入清单，返回 (state, triggered_count)。"""
        from .monitor_up import fetch_up_status
        cfg = rule["config"]
        mid = int(cfg["mid"])
        items_cfg = cfg.get("items") or ["live"]
        cookie = (self.config.get("cookie", "") or "")
        state = fetch_up_status(mid, cookie=cookie)
        rid = rule["id"]
        last_raw = self.storage.get_meta(f"up_state:{rid}")
        last = json.loads(last_raw) if last_raw else {}
        name = state.get("name") or last.get("name") or f"UP{mid}"
        triggered: list[dict] = []
        matched = [f"UP监控:{name}"]
        sub_id = "monitor_alerts"

        # 直播状态：0 → 1 时触发（单独一条直播卡）
        if "live" in items_cfg:
            cur_status = int(state.get("live_status") or 0)
            prev_status = int(last.get("live_status") or 0)
            if prev_status == 0 and cur_status == 1:
                triggered.append({
                    "video_id": f"up_live_{mid}_{int(time.time())}",
                    "title": f"📺 {name} 开播：{state.get('live_title') or '直播中'}",
                    "url": state.get("live_url") or f"https://live.bilibili.com/",
                    "cover": state.get("face") or "",
                    "author": name,
                    "published_at": int(time.time()),
                    "matched": [f"UP监控:{name}"],
                })

        # 数据项：始终显示全部配置项（变化项带增量）
        metrics: list[dict] = []
        for field, label in (("following", "关注"), ("follower", "粉丝"),
                             ("view", "播放"), ("likes", "获赞")):
            if field not in items_cfg:
                continue
            cur = state.get(field)
            if cur is None:
                continue
            cur = int(cur)
            delta = ""
            prev = last.get(field)
            if prev is not None:
                prev = int(prev)
                if cur != prev:
                    d = cur - prev
                    sign = "+" if d > 0 else ""
                    delta = f"{sign}{d:,}"
            metrics.append({"label": label, "value": f"{cur:,}", "delta": delta})
        if metrics:
            triggered.append({
                "video_id": f"up_data_{mid}_{int(time.time())}",
                "title": f"📊 {name} 数据监控",
                "url": f"https://space.bilibili.com/{mid}/",
                "cover": state.get("face") or "",
                "author": name,
                "published_at": int(time.time()),
                "matched": metrics,
            })

        # 监控数据放监控页展示（不入待办）：存结构化 JSON 供前端渲染指标块
        display = {
            "kind": "up",
            "name": name,
            "mid": mid,
            "face": state.get("face", ""),
            "live_status": state.get("live_status", 0),
            "live_title": state.get("live_title", ""),
            "live_url": state.get("live_url", ""),
            "metrics": metrics,
        }
        self.storage.set_monitor_value(rid, json.dumps(display, ensure_ascii=False),
                                       int(time.time()), state.get("error", ""))
        self.storage.set_meta(f"up_state:{rid}", json.dumps({
            "name": name,
            "live_status": state.get("live_status"),
            "following": state.get("following"),
            "follower": state.get("follower"),
            "view": state.get("view"),
            "likes": state.get("likes"),
        }, ensure_ascii=False))
        return state, len(triggered)

    def _check_bili_stat_rule(self, rule: dict) -> tuple[dict, int]:
        """视频指标监控（bilibili_stat）：拉 view 统计 → 多字段变化合并一条卡片（封面=视频封面）。"""
        from core.bilibili_stats import reply_count
        cfg = rule["config"]
        aid = int(cfg.get("aid") or 0)
        fields = cfg.get("fields") or ["view"]
        cookie = (self.config.get("cookie", "") or "")
        rid = rule["id"]
        last_raw = self.storage.get_meta(f"up_state:{rid}")
        last = json.loads(last_raw) if last_raw else {}
        state = {"error": "", "title": last.get("title", ""), "pic": last.get("pic", "")}
        info = None

        try:
            if "reply" in fields:
                reply_v, reply_e = reply_count(aid, cookie=cookie)
                if reply_e:
                    state["error"] = reply_e
                else:
                    state["reply"] = int(reply_v or 0)
            info, e = view_stat_by_aid(aid, cookie)
            if info:
                state["title"] = info.get("title") or state["title"]
                state["pic"] = info.get("pic") or state["pic"]
                stat = info.get("stat") or {}
                state.update({f: int(stat.get(f) or 0) for f in fields if f != "reply"})
            elif e:
                state["error"] = (state["error"] + " | " if state["error"] else "") + e
        except Exception as e:
            state["error"] = (state["error"] + " | " if state["error"] else "") + f"view 异常: {e}"

        name = state.get("title") or f"视频 {aid}"
        metrics: list[dict] = []
        label_map = {"view": "播放", "like": "点赞", "coin": "投币", "favorite": "收藏",
                     "share": "转发", "danmaku": "弹幕", "reply": "评论"}
        for f in fields:
            cur = state.get(f)
            if cur is None:
                continue
            cur = int(cur)
            delta = ""
            prev = last.get(f)
            if prev is not None:
                prev = int(prev)
                if cur != prev:
                    d = cur - prev
                    sign = "+" if d > 0 else ""
                    delta = f"{sign}{d:,}"
            metrics.append({"label": label_map.get(f, f), "value": f"{cur:,}", "delta": delta})
        triggered: list[dict] = []
        if metrics:
            triggered.append({
                "video_id": f"vid_{aid}_{int(time.time())}",
                "title": f"📊 {name}",
                "url": f"https://www.bilibili.com/video/av{aid}",
                "cover": state.get("pic") or "",
                "author": (info or {}).get("author", ""),
                "published_at": int(time.time()),
                "matched": metrics,
            })
        # 监控数据放监控页展示（不入待办）：存结构化 JSON 供前端渲染指标块
        display = {
            "kind": "stat",
            "title": name,
            "aid": aid,
            "pic": state.get("pic", ""),
            "metrics": metrics,
        }
        self.storage.set_monitor_value(rid, json.dumps(display, ensure_ascii=False),
                                       int(time.time()), state.get("error", ""))
        self.storage.set_meta(f"up_state:{rid}", json.dumps({
            "title": state.get("title"), "pic": state.get("pic"),
            **{f: state.get(f) for f in fields if state.get(f) is not None},
        }, ensure_ascii=False))
        return state, len(triggered)

    def _check_github_rule(self, rule: dict) -> tuple[dict, int]:
        """GitHub 仓库监控（github_repo）：走 api.github.com 拿 stars/forks/issues/watchers/downloads。"""
        import requests
        cfg = rule["config"]
        owner, repo = cfg.get("owner", ""), cfg.get("repo", "")
        fields = cfg.get("fields") or ["stars"]
        rid = rule["id"]
        last_raw = self.storage.get_meta(f"up_state:{rid}")
        last = json.loads(last_raw) if last_raw else {}
        state = {"error": "", "title": repo, "owner": owner}
        gh_headers = {"User-Agent": "Mozilla/5.0", "Accept": "application/vnd.github+json"}

        try:
            r = requests.get(f"https://api.github.com/repos/{owner}/{repo}",
                             headers=gh_headers, timeout=15)
            if r.status_code == 404:
                state["error"] = f"仓库 {owner}/{repo} 不存在"
            elif r.status_code != 200:
                state["error"] = f"GitHub API HTTP {r.status_code}"
            else:
                d = r.json()
                state["title"] = d.get("full_name") or repo
                state["owner"] = owner
                state["desc"] = (d.get("description") or "")[:80]
                for f in fields:
                    if f == "stars":
                        state["stars"] = int(d.get("stargazers_count") or 0)
                    elif f == "forks":
                        state["forks"] = int(d.get("forks_count") or 0)
                    elif f == "issues":
                        state["issues"] = int(d.get("open_issues_count") or 0)
                    elif f == "watchers":
                        state["watchers"] = int(d.get("subscribers_count") or 0)
            if "downloads" in fields and not state["error"]:
                rr = requests.get(f"https://api.github.com/repos/{owner}/{repo}/releases/latest",
                                  headers=gh_headers, timeout=15)
                if rr.status_code == 200:
                    rel = rr.json()
                    state["downloads"] = sum(int(a.get("download_count") or 0)
                                             for a in rel.get("assets", []))
                # 404 = 无 release，视为 0；其他错误忽略
        except Exception as e:
            state["error"] = (state["error"] + " | " if state["error"] else "") + f"github 异常: {e}"

        label_map = {"stars": "星标", "forks": "复刻", "issues": "议题",
                     "watchers": "关注", "downloads": "下载"}
        metrics: list[dict] = []
        for f in fields:
            cur = state.get(f)
            if cur is None:
                continue
            cur = int(cur)
            delta = ""
            prev = last.get(f)
            if prev is not None:
                prev = int(prev)
                if cur != prev:
                    d = cur - prev
                    sign = "+" if d > 0 else ""
                    delta = f"{sign}{d:,}"
            metrics.append({"label": label_map.get(f, f), "value": f"{cur:,}", "delta": delta})
        display = {
            "kind": "github",
            "title": state.get("title") or f"{owner}/{repo}",
            "owner": state.get("owner", owner),
            "repo": repo,
            "desc": state.get("desc", ""),
            "metrics": metrics,
        }
        self.storage.set_monitor_value(rid, json.dumps(display, ensure_ascii=False),
                                       int(time.time()), state.get("error", ""))
        self.storage.set_meta(f"up_state:{rid}", json.dumps(
            {f: state.get(f) for f in fields if state.get(f) is not None},
            ensure_ascii=False))
        return state, 0

    def check(self) -> None:
        now = time.time()
        for rule in self.rules.all(include_disabled=False):
            rid = rule["id"]
            if rid in self._running:
                continue
            interval = int(rule.get("refresh_interval_minutes") or
                           self.config.get("default_refresh_minutes", 60))
            if now - self._last.get(rid, 0) < interval * 60:
                continue
            self._running.add(rid)
            try:
                rtype = rule.get("config", {}).get("type", "xpath")
                if rtype == "up":
                    state, n = self._check_up_rule(rule)
                elif rtype == "bilibili_stat":
                    state, n = self._check_bili_stat_rule(rule)
                elif rtype == "github_repo":
                    state, n = self._check_github_rule(rule)
                else:
                    state, n = {}, 0
                    value, error = scrape_rule(rule, self.config.all())
                    self.storage.set_monitor_value(rid, value, int(time.time()), error)
                    if error:
                        log.warning("监控 %s: %s", rule.get("name"), error)
                self._last[rid] = time.time()
                self._save_last(rid)
                if state.get("error"):
                    log.warning("监控 %s: %s", rule.get("name"), state["error"])
            finally:
                self._running.discard(rid)

    def is_busy(self) -> bool:
        return bool(self._running)

    def refresh_one(self, rule_id: str) -> dict:
        rule = self.rules.get(rule_id)
        if not rule:
            return {"ok": False, "error": "规则不存在"}
        rtype = rule.get("config", {}).get("type", "xpath")
        if rtype == "up":
            state, n = self._check_up_rule(rule)
        elif rtype == "bilibili_stat":
            state, n = self._check_bili_stat_rule(rule)
        elif rtype == "github_repo":
            state, n = self._check_github_rule(rule)
        else:
            value, error = scrape_rule(rule, self.config.all())
            self.storage.set_monitor_value(rule_id, value, int(time.time()), error)
            self._last[rule_id] = time.time()
            self._save_last(rule_id)
            return {"ok": not error, "value": value, "error": error}
        self._last[rule_id] = time.time()
        self._save_last(rule_id)
        return {"ok": True, "value": json.dumps(state, ensure_ascii=False),
                "triggered": n, "error": state.get("error", "")}

    def refresh_all(self) -> dict[str, dict]:
        out = {}
        for rule in self.rules.all():
            out[rule["id"]] = self.refresh_one(rule["id"])
        return out


def _strip_ws(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()
