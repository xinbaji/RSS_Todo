"""B 站视频统计：view API（播放/点赞/投币/收藏/转发/弹幕） + reply API（评论数）。

公开接口在无 Cookie 时也能拿到基本数据，但可能被风控；reply API 需要 WBI 签名（复用 core.adapters.bilibili 的密钥与签名算法）。
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass

import requests

from core.adapters.bilibili import NAV_URL, UA, _enc_wbi, _mixin_key

log = logging.getLogger("rss-todo.bili-stats")

VIEW_URL = "https://api.bilibili.com/x/web-interface/view"
REPLY_URL = "https://api.bilibili.com/x/v2/reply/wbi/main"

_KEYS: tuple[str, str] | None = None
_KEYS_AT: float = 0.0


def _get_keys() -> tuple[str, str]:
    global _KEYS, _KEYS_AT
    if _KEYS and time.time() - _KEYS_AT < 86400:
        return _KEYS
    resp = requests.get(NAV_URL, headers={"User-Agent": UA}, timeout=10)
    data = resp.json()
    wbi = (data.get("data") or {}).get("wbi_img")
    if not wbi or not wbi.get("img_url") or not wbi.get("sub_url"):
        raise RuntimeError(f"nav 失败: code={data.get('code')}")
    _KEYS = (
        wbi["img_url"].rsplit("/", 1)[-1].split(".")[0],
        wbi["sub_url"].rsplit("/", 1)[-1].split(".")[0],
    )
    _KEYS_AT = time.time()
    return _KEYS


# 视频页可拿的指标：source + 抓取时使用的字段
VIEW_STAT_FIELDS = {
    "播放量": ("view",),
    "点赞": ("like",),
    "投币": ("coin",),
    "收藏": ("favorite",),
    "转发": ("share",),
    "弹幕": ("danmaku",),
}
REPLY_FIELD = "评论数"

ALL_FIELDS = [
    ("播放量", "view", "view_stat"),
    ("点赞", "like", "view_stat"),
    ("投币", "coin", "view_stat"),
    ("收藏", "favorite", "view_stat"),
    ("转发", "share", "view_stat"),
    ("弹幕", "danmaku", "view_stat"),
    ("评论数", "reply", "reply_api"),
]


def extract_bvid(url: str) -> str | None:
    """从 B 站视频页 URL 提取 BV 号（支持 ? 参数与短链以外的常见形态）。"""
    m = re.search(r"/video/(BV[a-zA-Z0-9]+)", url or "")
    if m:
        return m.group(1)
    return None


def view_stat(bvid: str, cookie: str = "") -> tuple[dict | None, str]:
    """调 view API 返回 stat 字典 + 标题作者；失败 (None, error)。"""
    headers = {"User-Agent": UA, "Referer": f"https://www.bilibili.com/video/{bvid}"}
    if cookie:
        headers["Cookie"] = cookie
    try:
        resp = requests.get(VIEW_URL, params={"bvid": bvid}, headers=headers, timeout=15)
        data = resp.json()
    except (requests.RequestException, ValueError) as e:
        return None, f"view 请求失败: {e}"
    if data.get("code") != 0:
        return None, f"view API code={data.get('code')}: {data.get('message')}"
    d = data["data"]
    return {
        "title": d.get("title"),
        "author": d.get("owner", {}).get("name"),
        "aid": d.get("aid"),
        "pic": d.get("pic", ""),
        "stat": dict(d.get("stat") or {}),
    }, ""


def reply_count(aid: int, cookie: str = "") -> tuple[int | None, str]:
    """调 reply/wbi/main API 取 data.cursor.allcount。"""
    try:
        img_key, sub_key = _get_keys()
    except Exception as e:
        return None, f"取 WBI 密钥失败: {e}"
    params = {
        "oid": aid, "type": 1, "mode": 3, "plat": 1,
        "web_location": "1315875",
        "pagination_str": '{"offset":""}',
        "seek_rpid": "",
    }
    params = _enc_wbi(params, img_key, sub_key)
    headers = {"User-Agent": UA, "Referer": f"https://www.bilibili.com/video/" }
    if cookie:
        headers["Cookie"] = cookie
    try:
        resp = requests.get(REPLY_URL, params=params, headers=headers, timeout=15)
        data = resp.json()
    except (requests.RequestException, ValueError) as e:
        return None, f"reply 请求失败: {e}"
    if data.get("code") != 0:
        return None, f"reply API code={data.get('code')}: {data.get('message')}"
    cursor = data["data"]["cursor"]
    # 兼容 all_count（新）与 allcount（旧）两种字段名
    val = cursor.get("all_count") or cursor.get("allcount") or 0
    return int(val), ""


@dataclass
class InspectResult:
    ok: bool
    url: str
    bvid: str | None = None
    aid: int | None = None
    title: str | None = None
    author: str | None = None
    metrics: dict | None = None  # {"播放量": "4186", "评论数": None, ...}
    errors: dict | None = None   # {"评论数": "风控 ...", ...}


def inspect(url: str, cookie: str = "") -> InspectResult:
    """智能识别视频页 URL：返回 BV/aid/标题/可拿到的指标及抓取错误。"""
    bvid = extract_bvid(url)
    if not bvid:
        return InspectResult(ok=False, url=url, errors={"url": "非 B 站视频页 URL"})
    info, err = view_stat(bvid, cookie)
    metrics: dict = {}
    errs: dict = {}
    if not info:
        return InspectResult(ok=False, url=url, bvid=bvid, errors={"view": err})
    stat = info["stat"]
    for label, (field, _) in [(n, ("", "view_stat")) for n, _ in
                              [("播放量", ("view", "view_stat")), ("点赞", ("like", "view_stat")),
                               ("投币", ("coin", "view_stat")), ("收藏", ("favorite", "view_stat")),
                               ("转发", ("share", "view_stat")), ("弹幕", ("danmaku", "view_stat"))]]:
        # 简化：直接 stat.get
        pass
    mapping = {"播放量": "view", "点赞": "like", "投币": "coin",
               "收藏": "favorite", "转发": "share", "弹幕": "danmaku"}
    for label, field in mapping.items():
        v = stat.get(field)
        metrics[label] = str(v) if v is not None else None
    aid = info["aid"]
    # 评论数（reply）
    rc, re_err = reply_count(aid, cookie)
    metrics[REPLY_FIELD] = str(rc) if rc is not None else None
    if re_err:
        errs[REPLY_FIELD] = re_err
    return InspectResult(ok=True, url=url, bvid=bvid, aid=aid,
                         title=info["title"], author=info["author"],
                         metrics=metrics, errors=errs or None)