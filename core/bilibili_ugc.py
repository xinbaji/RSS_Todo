"""B 站视频合集解析：分P视频一次性导入待办。

- view 接口（公开，无需 WBI 签名）拿 bvid 详情（aid/title/pic/owner/pages）
- 合集就是 pages 数组 ≥ 2 的视频
- 不创建订阅，items 用固定 sub_id="ugc_import" 标识
"""
from __future__ import annotations

import re

import requests

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
VIEW_URL = "https://api.bilibili.com/x/web-interface/view"
UGC_SUB_ID = "ugc_import"  # 合集导入专用合成订阅 id


def extract_bvid(url: str) -> str | None:
    """从任意 B 站视频链接里抠 BV 号。"""
    m = re.search(r"(BV[1-9A-HJ-NP-Za-km-z]{10})", url or "")
    return m.group(1) if m else None


def parse_collection(url: str, cookie: str = "") -> dict:
    """解析视频详情（含分P列表）。

    Returns: {bvid, aid, title, pic, desc, duration, up_name, up_uid, pages:[...]}
    Raises: ValueError（无 BV 号）/ RuntimeError（接口失败）
    """
    bvid = extract_bvid(url)
    if not bvid:
        raise ValueError("未识别到 BV 号")
    h = {"User-Agent": UA, "Referer": "https://www.bilibili.com/"}
    if cookie:
        h["Cookie"] = cookie
    r = requests.get(VIEW_URL, params={"bvid": bvid}, headers=h, timeout=15)
    data = r.json()
    if data.get("code") != 0:
        raise RuntimeError(f"B 站 view 接口失败: {data.get('message')}")
    d = data["data"] or {}
    owner = d.get("owner") or {}
    pages = [{"page": p.get("page", 0), "cid": p.get("cid", 0),
              "part": p.get("part", ""), "duration": p.get("duration", 0)}
             for p in (d.get("pages") or [])]
    return {
        "bvid": bvid,
        "aid": d.get("aid"),
        "title": d.get("title", ""),
        "pic": d.get("pic", ""),
        "desc": d.get("desc", ""),
        "duration": d.get("duration", 0),
        "pubdate": d.get("pubdate", 0),
        "up_name": owner.get("name", ""),
        "up_uid": owner.get("mid", 0),
        "pages": pages,
    }
