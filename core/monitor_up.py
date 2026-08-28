"""B 站 UP 主监控：直播状态、粉丝数、播放量、获赞数。

数据源：
  - acc/info  WBI 签名：data.live_room
  - relation/stat：data.following / data.follower
  - upstat：data.archive.view / data.likes

mid 解析复用 bilibili adapter 的 space url 正则。
"""
from __future__ import annotations

import re
import time
import urllib.parse

import requests

from .adapters.bilibili import NAV_URL, UA, _enc_wbi

LIVE_URL = "https://api.bilibili.com/x/space/wbi/acc/info"
RELATION_URL = "https://api.bilibili.com/x/relation/stat"
UPSTAT_URL = "https://api.bilibili.com/x/space/upstat"


def parse_up(url_or_uid: str) -> dict:
    """从 UP 主页链接或纯 UID 解析出 {mid, name}。name 留空，刷新后填。"""
    s = (url_or_uid or "").strip()
    m = re.search(r"space\.bilibili\.com/(\d+)", s)
    if m:
        return {"mid": int(m.group(1))}
    if re.fullmatch(r"\d{4,12}", s):
        return {"mid": int(s)}
    raise ValueError("未识别到 UP 主页链接或 UID")


def _get_wbi_keys() -> tuple[str, str]:
    """从 nav 取 wbi 密钥（缓存到调用方）。"""
    r = requests.get(NAV_URL, headers={"User-Agent": UA}, timeout=10)
    wbi = (r.json().get("data") or {}).get("wbi_img") or {}
    return (wbi["img_url"].rsplit("/", 1)[-1].split(".")[0],
            wbi["sub_url"].rsplit("/", 1)[-1].split(".")[0])


def fetch_up_name(mid: int, cookie: str = "") -> str:
    """轻量获取 UP 昵称（只调 acc/info），失败返回空字符串。"""
    h = {"User-Agent": UA, "Referer": f"https://space.bilibili.com/{mid}/"}
    if cookie:
        h["Cookie"] = cookie
    try:
        img, sub = _get_wbi_keys()
        base = {"mid": mid, "token": "", "platform": "web", "web_location": 1550101,
                "dm_img_list": "[]",
                "dm_img_str": "V2ViR0wgMS4wIChPcGVuR0wgRVMgMi4wIENocm9taXVtKQ",
                "dm_cover_img_str": "QU5HTEUgKE5WSURJQSwgTlZJRElBIEdlRm9yY2UgUlRYIDQwNjAgTGFwdG9wIEdQVSAoMHgwMDAwMjhFMCkgRGlyZWN0M0QxMSB2c181XzAgcHNfNV8wLCBEM0QxMSlHb29nbGUgSW5jLiAoTlZJRElBKQ",
                "dm_img_inter": '{"ds":[],"wh":[3417,2209,97],"of":[500,1000,500]}'}
        params = _enc_wbi(base, img, sub)
        r = requests.get(LIVE_URL, params=params, headers=h, timeout=15)
        d = r.json()
        if d.get("code") == 0:
            return str((d.get("data") or {}).get("name", "") or "")
    except Exception:
        pass
    return ""


def fetch_up_status(mid: int, cookie: str = "") -> dict:
    """拉取 UP 主状态：返回 {mid, name, live_status, live_title, live_url, live_cover,
                               following, follower, view, likes, error}"""
    h = {"User-Agent": UA,
         "Referer": f"https://space.bilibili.com/{mid}/"}
    if cookie:
        h["Cookie"] = cookie
    out: dict = {"mid": mid, "live_status": 0, "face": "",
                 "live_title": "", "live_url": "", "live_cover": "",
                 "following": None, "follower": None,
                 "view": None, "likes": None, "error": ""}
    try:
        img, sub = _get_wbi_keys()
        base = {"mid": mid, "token": "", "platform": "web", "web_location": 1550101,
                "dm_img_list": "[]",
                "dm_img_str": "V2ViR0wgMS4wIChPcGVuR0wgRVMgMi4wIENocm9taXVtKQ",
                "dm_cover_img_str": "QU5HTEUgKE5WSURJQSwgTlZJRElBIEdlRm9yY2UgUlRYIDQwNjAgTGFwdG9wIEdQVSAoMHgwMDAwMjhFMCkgRGlyZWN0M0QxMSB2c181XzAgcHNfNV8wLCBEM0QxMSlHb29nbGUgSW5jLiAoTlZJRElBKQ",
                "dm_img_inter": '{"ds":[],"wh":[3417,2209,97],"of":[500,1000,500]}'}
        params = _enc_wbi(base, img, sub)
        r = requests.get(LIVE_URL, params=params, headers=h, timeout=15)
        d = r.json()
        if d.get("code") == 0:
            data = d.get("data") or {}
            out["name"] = data.get("name", "")
            out["face"] = data.get("face", "") or ""
            lr = data.get("live_room") or {}
            out["live_status"] = int(lr.get("liveStatus", 0) or 0)
            out["live_title"] = lr.get("title", "") or ""
            out["live_url"] = lr.get("url", "") or ""
            out["live_cover"] = lr.get("cover", "") or ""
        else:
            out["error"] = f"acc/info: {d.get('message')}"
    except Exception as e:
        out["error"] = f"acc/info 异常: {e}"
    # relation/stat
    try:
        r = requests.get(RELATION_URL, params={"vmid": mid, "web_location": 333.1387},
                         headers=h, timeout=15)
        d = r.json()
        if d.get("code") == 0:
            data = d.get("data") or {}
            out["following"] = int(data.get("following") or 0)
            out["follower"] = int(data.get("follower") or 0)
        else:
            out["error"] = (out["error"] + " | " if out["error"] else "") + f"relation: {d.get('message')}"
    except Exception as e:
        out["error"] = (out["error"] + " | " if out["error"] else "") + f"relation 异常: {e}"
    # upstat
    try:
        r = requests.get(UPSTAT_URL, params={"mid": mid, "web_location": 333.1387},
                         headers=h, timeout=15)
        d = r.json()
        if d.get("code") == 0:
            data = d.get("data") or {}
            out["view"] = int((data.get("archive") or {}).get("view") or 0)
            out["likes"] = int(data.get("likes") or 0)
        else:
            out["error"] = (out["error"] + " | " if out["error"] else "") + f"upstat: {d.get('message')}"
    except Exception as e:
        out["error"] = (out["error"] + " | " if out["error"] else "") + f"upstat 异常: {e}"
    return out
