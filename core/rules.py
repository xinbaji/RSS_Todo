"""订阅规则模型 + JSON 加载/校验/写回。

subscriptions.json 是用户要求的"订阅逻辑抽象"：
  {
    "version": 1,
    "subscriptions": [
      {
        "id": "bili-001", "name": "...", "adapter": "bilibili",
        "enabled": true, "refresh_interval_minutes": 60,
        "config": { "uid": 123, "fetch_depth": 30,
                    "keywords": [{"text": "...", "regex": false, "case_sensitive": false}],
                    "match_logic": "any" }
      }
    ]
  }
"""
from __future__ import annotations

import json
import logging
import re
import uuid
from pathlib import Path

log = logging.getLogger("rss-todo.rules")

VALID_ADAPTERS = ("bilibili", "ugc")  # ugc = 合集导入合成订阅（不参与抓取）
VALID_MATCH_LOGIC = ("any", "all")
VALID_FETCH_MODES = ("latest", "full")
# 全量模式默认刷新间隔（分钟）：全量翻页抓取较重，拉长周期降低风控风险
FULL_MODE_DEFAULT_INTERVAL = 180


class RuleError(ValueError):
    pass


def _new_id() -> str:
    return uuid.uuid4().hex[:10]


def _new_storage(data_dir) -> "object":
    """延迟创建 Storage（避免 rules ↔ storage 循环导入）。"""
    from core.storage import Storage
    return Storage(data_dir)


def _norm_keywords(keywords) -> list:
    out = []
    for kw in keywords:
        if isinstance(kw, str):
            out.append({"text": kw, "regex": False, "case_sensitive": False})
        elif isinstance(kw, dict) and kw.get("text"):
            out.append({
                "text": str(kw["text"]).strip(),
                "regex": bool(kw.get("regex", False)),
                "case_sensitive": bool(kw.get("case_sensitive", False)),
            })
    return [k for k in out if k["text"]]


def normalize_subscription(raw: dict, idx: int = 0) -> dict:
    """校验并补全单条订阅，返回规范结构；非法则抛 RuleError。"""
    if not isinstance(raw, dict):
        raise RuleError(f"[{idx}] 订阅必须是对象")
    adapter = raw.get("adapter", "bilibili")
    if adapter not in VALID_ADAPTERS:
        raise RuleError(f"[{idx}] 不支持的 adapter: {adapter}")
    name = str(raw.get("name", "")).strip() or f"订阅 {idx + 1}"
    cfg_raw = raw.get("config") or {}
    uid = cfg_raw.get("uid")
    bvid = str(cfg_raw.get("bvid", "") or "").strip()
    if adapter == "bilibili" and not uid:
        raise RuleError(f"[{idx}] bilibili 订阅缺少 config.uid")
    if adapter == "ugc" and not bvid and str(raw.get("id") or "") != "ugc_import":
        # ugc_import 是合集导入的合成订阅标记（无 bvid，不参与抓取）
        raise RuleError(f"[{idx}] 合集订阅缺少 config.bvid")
    match_logic = cfg_raw.get("match_logic", "all")  # 产品固定默认：全部命中
    if match_logic not in VALID_MATCH_LOGIC:
        match_logic = "all"
    fetch_mode = cfg_raw.get("fetch_mode", "latest")
    if fetch_mode not in VALID_FETCH_MODES:
        fetch_mode = "latest"
    keywords = cfg_raw.get("keywords", [])
    if isinstance(keywords, str):  # 兼容误传单个字符串：视为单关键词
        keywords = [keywords]
    if not isinstance(keywords, list):
        raise RuleError(f"[{idx}] keywords 必须是列表")
    keywords = _norm_keywords(keywords)
    # 关键词允许为空：空 = 跟踪该订阅最新 fetch_depth 条视频（scheduler 全量入待办，去重）
    interval = int(raw.get("refresh_interval_minutes", 0) or 0)
    if interval <= 0:
        # 全量模式未显式配置间隔时，默认拉长（风控）
        interval = FULL_MODE_DEFAULT_INTERVAL if fetch_mode == "full" else 0
    return {
        "id": str(raw.get("id") or _new_id()),
        "name": name,
        "adapter": adapter,
        "enabled": bool(raw.get("enabled", True)),
        "refresh_interval_minutes": interval if interval > 0 else None,  # None -> 全局默认
        "config": {
            **({"uid": int(uid)} if uid is not None else {}),
            **({"bvid": bvid} if bvid else {}),
            "up_name": str(cfg_raw.get("up_name", "") or ""),
            "fetch_depth": max(1, min(int(cfg_raw.get("fetch_depth", 30) or 30), 1000)),
            "fetch_mode": fetch_mode,
            "page_interval_seconds": float(
                cfg_raw.get("page_interval_seconds", 5) or 5),
            "keywords": keywords,
            "exclude_keywords": _norm_keywords(cfg_raw.get("exclude_keywords", []) or []),
            "match_logic": match_logic,
        },
    }


class Subscriptions:
    """订阅规则集合：加载 / 保存 / CRUD（存储于 app.db，旧 JSON 一次性迁移备份）。

    兼容两种构造方式：
      - Subscriptions(data_dir)            # 独立使用（测试/脚本），内部自建 Storage
      - Subscriptions(storage=storage)     # 应用内复用 SQLite 连接
    """

    def __init__(self, data_dir: str | Path = "data", storage=None):
        self.data_dir = Path(data_dir)
        self.path = self.data_dir / "subscriptions.json"  # 仅作历史迁移源
        self._storage = storage if storage is not None else _new_storage(data_dir)
        self._list: list[dict] = []
        self.load()

    def load(self) -> None:
        rows = self._storage.list_subscriptions()
        if rows:
            self._list = rows
            return
        # db 无数据：尝试从历史 JSON 一次性迁移（导入后备份原文件）
        if self.path.exists():
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except (json.JSONDecodeError, OSError):
                data = None
            if isinstance(data, dict):
                for i, s in enumerate(data.get("subscriptions", [])):
                    try:
                        self._list.append(normalize_subscription(s, i))
                    except RuleError as e:
                        log.warning("跳过非法订阅 %s: %s", s.get("id"), e)
                if self._list:
                    self.save()
                try:
                    self.path.replace(self.path.with_suffix(".json.bak"))
                    log.info("subscriptions.json 已迁移至 app.db（原文件备份为 .json.bak）")
                except OSError:
                    pass
        self._list = self._storage.list_subscriptions()

    def save(self) -> None:
        self._storage.replace_subscriptions(self._list)

    def close(self) -> None:
        """释放底层 SQLite 连接（应用内复用连接时由 app 统一管理，测试/脚本独立使用时调用）。"""
        if self._storage is not None and hasattr(self._storage, "close"):
            self._storage.close()

    def all(self, include_disabled: bool = True) -> list[dict]:
        if include_disabled:
            return [dict(s) for s in self._list]
        return [dict(s) for s in self._list if s.get("enabled", True)]

    def get(self, sub_id: str) -> dict | None:
        for s in self._list:
            if s["id"] == sub_id:
                return dict(s)
        return None

    def add(self, raw: dict) -> dict:
        sub = normalize_subscription(raw, len(self._list))
        if any(s["id"] == sub["id"] for s in self._list):
            raise RuleError(f"订阅 id 已存在: {sub['id']}")
        self._list.append(sub)
        self.save()
        return dict(sub)

    def update(self, sub_id: str, raw: dict) -> dict:
        idx = next((i for i, s in enumerate(self._list) if s["id"] == sub_id), None)
        if idx is None:
            raise RuleError(f"订阅不存在: {sub_id}")
        raw = dict(raw)
        raw["id"] = sub_id
        sub = normalize_subscription(raw, idx)
        self._list[idx] = sub
        self.save()
        return dict(sub)

    def remove(self, sub_id: str) -> bool:
        before = len(self._list)
        self._list = [s for s in self._list if s["id"] != sub_id]
        changed = len(self._list) != before
        if changed:
            self.save()
        return changed


def parse_uid_from_url(url: str) -> int | None:
    """从 B 站空间链接解析 UID。支持 space.bilibili.com/{uid}、b23.tv 短链等。"""
    url = (url or "").strip()
    m = re.search(r"space\.bilibili\.com/(\d+)", url)
    if m:
        return int(m.group(1))
    m = re.search(r"(?:^|[^0-9])(\d{6,12})(?:[^0-9]|$)", url)
    if m:
        return int(m.group(1))
    return None
