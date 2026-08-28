"""B 站适配器：公开 API + WBI 签名，可选 Cookie。

接口: GET https://api.bilibili.com/x/space/wbi/arc/search
风控: 无 Cookie 高频易 412，默认低频 + 指数退避重试。
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
import urllib.parse

import requests

from .base import BaseAdapter, VideoItem

log = logging.getLogger("rss-todo.bilibili")

# 系统死代理会拖垮请求（127.0.0.1:7897 常死），强制直连
for _k in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
    os.environ.pop(_k, None)

MIXIN_KEY_ENC_TAB = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35,
    27, 43, 5, 49, 33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13,
    37, 48, 7, 16, 24, 55, 40, 61, 26, 17, 0, 1, 60, 51, 30, 4,
    22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11, 36, 20, 34, 44, 52,
]

NAV_URL = "https://api.bilibili.com/x/web-interface/nav"
LIST_URL = "https://api.bilibili.com/x/space/wbi/arc/search"
_FULL_MAX_PAGES = 20  # 全量模式翻页上限（50 条/页 → 最多 1000 条），保护性限制

# B 站风控指纹参数（模拟真实浏览器指纹，配合 wbi 签名降低风控）
DM_IMG_STR = "V2ViR0wgMS4wIChPcGVuR0wgRVMgMi4wIENocm9taXVtKQ"  # base64(WebGL 1.0 (OpenGL ES 2.0 Chromium))
DM_COVER_IMG_STR = "QU5HTEUgKE5WSURJQSwgTlZJRElBIEdlRm9yY2UgUlRYIDQwNjAgTGFwdG9wIEdQVSAoMHgwMDAwMjhFMCkgRGlyZWN0M0QxMSB2c181XzAgcHNfNV8wLCBEM0QxMSlHb29nbGUgSW5jLiAoTlZJRElBKQ"
# dm_img_inter：真实浏览器形态（ds 含"点击搜索图标"事件，wh=屏幕尺寸, of=视口偏移）
DM_IMG_INTER = ('{"ds":[{"t":0,"c":"dnVpX2ljb24gc2ljLUJEQy1tYWduaWZpZXJfc2VhcmNoX2xpbmUgc2Vhcm",'
                '"p":[1544,30,423],"s":[140,220,280]}],"wh":[3417,2209,97],"of":[500,1000,500]}')


def _gen_mouse_trace(n: int = 40) -> str:
    """生成仿真鼠标轨迹（dm_img_list 形态：时间递增的坐标事件序列，紧凑 JSON）。

    空数组会被 B 站判定"无浏览器行为"而忽略 keyword，所以必须有真实形态的事件流。
    固定种子保证每次请求生成一致的数据。
    """
    import random
    rnd = random.Random(20260828)
    out = []
    t = 1500
    x, y = 1200, 300
    for _ in range(n):
        x += rnd.randint(-60, 220)
        y += rnd.randint(-40, 160)
        x = max(0, min(6800, x))
        y = max(0, min(5600, y))
        t += rnd.randint(80, 240)
        out.append({"x": x, "y": y, "z": rnd.randint(0, 80),
                    "timestamp": t, "k": rnd.randint(60, 130),
                    "type": rnd.randint(0, 1)})
    return json.dumps(out, separators=(",", ":"))


DM_IMG_LIST = _gen_mouse_trace()


def _risk_params(index: int = 0) -> dict:
    """arc/search 通用风控参数（浏览器同款）。

    index 与页码对应：pn=1 → index=0、pn=2 → index=1（浏览器行为，B 站据此校验）。
    """
    return {
        "index": index,
        "order_avoided": "true",
        "web_location": "333.1387",
        "dm_img_list": DM_IMG_LIST,
        "dm_img_str": DM_IMG_STR,
        "dm_cover_img_str": DM_COVER_IMG_STR,
        "dm_img_inter": DM_IMG_INTER,
    }
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

# 风控/拦截类业务码（可退避重试）：-412 高频 / -352 请求被拦截 / -403 访问被拒 / -509 异常访问
RISK_CODES = (-412, -352, -403, -509)


def _classify_api(data, resp=None) -> str:
    """统一分类 API 响应：'ok' 成功；'risk' 疑似风控（可退避重试）；其他为不可重试错误描述。"""
    if resp is not None and resp.status_code in (403, 412):
        return "risk"  # HTTP 层拦截页
    if not isinstance(data, dict):
        return "risk"  # JSON 结构异常，疑似风控拦截
    code = data.get("code")
    if code == 0:
        return "ok"
    if code in RISK_CODES or code is None:
        return "risk"
    return f"code={code}: {data.get('message', '')}"


class BilibiliError(RuntimeError):
    """B 站接口业务错误（含风控）。"""


def _mixin_key(orig: str) -> str:
    return "".join(orig[i] for i in MIXIN_KEY_ENC_TAB)[:32]


def _enc_wbi(params: dict, img_key: str, sub_key: str) -> dict:
    mixin = _mixin_key(img_key + sub_key)
    params = dict(params)
    params["wts"] = int(time.time())
    params = dict(sorted(params.items()))
    # 过滤 WBI 规则不允许的字符
    params = {k: "".join(c for c in str(v) if c not in "!'()*") for k, v in params.items()}
    query = urllib.parse.urlencode(params)
    params["w_rid"] = hashlib.md5((query + mixin).encode("utf-8")).hexdigest()
    return params


class BilibiliAdapter(BaseAdapter):
    name = "bilibili"

    def __init__(self, config=None, global_config=None):
        super().__init__(config, global_config)
        self.uid = int(config.get("uid", 0))
        self.depth = int(config.get("fetch_depth", 30) or 30)
        self.cookie = (global_config or {}).get("cookie", "") or ""
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": UA, "Referer": f"https://space.bilibili.com/{self.uid}/"})
        if self.cookie:
            self._session.headers["Cookie"] = self.cookie
        self._keys: tuple[str, str] | None = None
        self._keys_at: float = 0.0

    # ---------- WBI 密钥 ----------
    def _get_keys(self) -> tuple[str, str]:
        """获取并缓存 WBI 密钥（约 1 天），失败自动重取。"""
        if self._keys and time.time() - self._keys_at < 86400:
            return self._keys
        resp = self._session.get(NAV_URL, timeout=10)
        data = resp.json()
        # 未登录时 code=-101，但 data.wbi_img 仍会返回密钥
        wbi = (data.get("data") or {}).get("wbi_img")
        if not wbi or not wbi.get("img_url") or not wbi.get("sub_url"):
            raise BilibiliError(f"nav 获取 WBI 密钥失败: code={data.get('code')} {data.get('message')}")
        wbi = data["data"]["wbi_img"]
        img_key = wbi["img_url"].rsplit("/", 1)[-1].split(".")[0]
        sub_key = wbi["sub_url"].rsplit("/", 1)[-1].split(".")[0]
        self._keys = (img_key, sub_key)
        self._keys_at = time.time()
        return self._keys

    # ---------- 抓取 ----------
    def fetch_videos(self) -> list[VideoItem]:
        """默认搜索模式：搜索失败/无合适关键词时自动回退到最新列表（仍按本地 all + 排除过滤）。
        fetch_mode=full 时搜索翻页，失败也回退。
        """
        search_kw = self._pick_search_keyword()
        mode = self.config.get("fetch_mode", "latest")
        if search_kw:
            try:
                if mode == "full":
                    return self._fetch_search_full(search_kw)
                return self._fetch_search(search_kw)  # arc/search + keyword（UP 内投稿搜索）
            except BilibiliError as e:
                log.warning("UP 内搜索失败，回退最新列表: %s", e)
        # 回退 / 无合适搜索词
        depth = min(max(int(self.config.get("fetch_depth", 30) or 30), 1), _FULL_MAX_PAGES * 50)
        return self._fetch_recent(depth)

    def _pick_search_keyword(self) -> str | None:
        """取包含关键词中第一个长度>2 的关键词作为搜索词。"""
        for k in self.config.get("keywords") or []:
            text = (k.get("text") if isinstance(k, dict) else str(k)).strip()
            if len(text) > 2:
                return text
        return None

    def _anon(self) -> requests.Session:
        """匿名会话：不带 Cookie。

        B 站行为：arc/search 的 keyword 只在匿名态生效（登录态会被忽略、返回最新列表），
        所以搜索请求必须匿名发；登录态只用于 nav 取密钥和最新列表（更稳）。
        """
        s = requests.Session()
        s.headers.update({"User-Agent": UA,
                          "Referer": f"https://space.bilibili.com/{self.uid}/"})
        return s

    def _fetch_search(self, keyword: str) -> list[VideoItem]:
        """UP 内投稿搜索单页：arc/search + keyword（WBI 签名）。

        登录态优先（B 站登录态下 keyword 有效）；无 Cookie 时才匿名（可用但易风控）。
        Referer 必须指向"空间搜索结果页"，否则 B 站判定非搜索行为、忽略 keyword。
        """
        img_key, sub_key = self._get_keys()
        last_err: Exception | None = None
        ps = min(max(int(self.config.get("fetch_depth", 30) or 30), 1), 50)
        sess = self._session if self.cookie else self._anon()
        search_ref = (f"https://space.bilibili.com/{self.uid}/search"
                      f"?keyword={urllib.parse.quote(keyword)}")
        for attempt in range(3):
            base = {
                "mid": self.uid,
                "keyword": keyword,
                "ps": ps,
                "pn": 1,
                "order": "pubdate",
                "platform": "web",
            }
            base.update(_risk_params(index=0))  # pn=1 → index=0
            params = _enc_wbi(base, img_key, sub_key)
            try:
                resp = sess.get(LIST_URL, params=params, timeout=15,
                                headers={"Referer": search_ref})
                data = resp.json()
            except (requests.RequestException, ValueError) as e:
                last_err = BilibiliError(f"网络异常/响应非 JSON（疑似风控拦截）: {e}")
                time.sleep([1, 3, 7][attempt])
                continue
            cls = _classify_api(data, resp)
            if cls == "ok":
                return self._parse_list(data)
            if cls == "risk":
                last_err = BilibiliError(f"风控 code={data.get('code')}: {data.get('message', '')}")
                time.sleep([1, 3, 7][attempt])
                continue
            raise BilibiliError(f"搜索失败: {cls}")
        raise BilibiliError(f"B 站搜索失败（疑似风控）: {last_err}")

    def _fetch_search_full(self, keyword: str) -> list[VideoItem]:
        """搜索翻页：每页 50，页间强制间隔，最多 _FULL_MAX_PAGES 页。登录态优先。"""
        page_interval = float(self.config.get("page_interval_seconds", 5) or 5)
        out: list[VideoItem] = []
        sess = self._session if self.cookie else self._anon()
        search_ref = (f"https://space.bilibili.com/{self.uid}/search"
                      f"?keyword={urllib.parse.quote(keyword)}")
        for pn in range(1, _FULL_MAX_PAGES + 1):
            img_key, sub_key = self._get_keys()
            last_err: Exception | None = None
            for attempt in range(3):
                base = {"mid": self.uid, "keyword": keyword, "ps": 50, "pn": pn,
                        "order": "pubdate", "platform": "web"}
                base.update(_risk_params(index=pn - 1))
                params = _enc_wbi(base, img_key, sub_key)
                try:
                    resp = sess.get(LIST_URL, params=params, timeout=15,
                                    headers={"Referer": search_ref})
                    data = resp.json()
                except (requests.RequestException, ValueError) as e:
                    last_err = BilibiliError(f"网络异常/响应非 JSON（疑似风控拦截）: {e}")
                    time.sleep([1, 3, 7][attempt])
                    continue
                cls = _classify_api(data, resp)
                if cls == "ok":
                    break
                if cls == "risk":
                    last_err = BilibiliError(f"风控 code={data.get('code')}: {data.get('message', '')}")
                    time.sleep([1, 3, 7][attempt])
                    continue
                raise BilibiliError(f"搜索失败: {cls}")
            else:
                raise BilibiliError(f"B 站搜索失败（疑似风控）: {last_err}")
            batch = self._parse_list(data)
            if not batch:
                break
            out.extend(batch)
            if len(batch) < 50:
                break
            if page_interval > 0:
                time.sleep(page_interval)
        return out

    def _fetch_recent(self, depth: int) -> list[VideoItem]:
        """按深度抓取：单页 50，不够则翻页直到凑够 depth 条。"""
        page_interval = float(self.config.get("page_interval_seconds", 5) or 5)
        out: list[VideoItem] = []
        pages = max(1, -(-depth // 50))  # ceil(depth/50)
        for pn in range(1, pages + 1):
            batch = self._fetch_page(pn, 50)
            out.extend(batch)
            if len(batch) < 50:  # 没更多了
                break
            if pn < pages and page_interval > 0:
                time.sleep(page_interval)
        return out[:depth]

    def _fetch_full(self) -> list[VideoItem]:
        """全量翻页：每页 50 条，页间强制间隔，最多 _FULL_MAX_PAGES 页（保护）。"""
        page_interval = float(self.config.get("page_interval_seconds", 5) or 5)
        out: list[VideoItem] = []
        pn = 1
        while pn <= _FULL_MAX_PAGES:
            batch = self._fetch_page(pn, 50)
            if not batch:
                break
            out.extend(batch)
            if len(batch) < 50:  # 最后一页
                break
            pn += 1
            if page_interval > 0:
                time.sleep(page_interval)
        return out

    def _fetch_page(self, pn: int, ps: int) -> list[VideoItem]:
        img_key, sub_key = self._get_keys()
        last_err: Exception | None = None
        for attempt in range(3):  # 退避重试：1s / 3s / 7s
            base = {  # 每次重试刷新 wts/w_rid
                "mid": self.uid,
                "ps": ps,
                "pn": pn,
                "order": "pubdate",
                "platform": "web",
            }
            base.update(_risk_params(index=pn - 1))
            params = _enc_wbi(base, img_key, sub_key)
            try:
                resp = self._session.get(LIST_URL, params=params, timeout=15)
                data = resp.json()
            except (requests.RequestException, ValueError) as e:
                last_err = BilibiliError(f"网络异常/响应非 JSON（疑似风控拦截）: {e}")
                time.sleep([1, 3, 7][attempt])
                continue
            cls = _classify_api(data, resp)
            if cls == "ok":
                return self._parse_list(data)
            if cls == "risk":
                last_err = BilibiliError(f"风控 code={data.get('code')}: {data.get('message', '')}")
                time.sleep([1, 3, 7][attempt])
                continue
            raise BilibiliError(cls)
        raise BilibiliError(f"B 站抓取失败（疑似风控）: {last_err}")

    def _parse_list(self, data: dict) -> list[VideoItem]:
        vlist = (data.get("data") or {}).get("list", {}).get("vlist", []) or []
        out = []
        for v in vlist:
            bvid = v.get("bvid", "")
            if not bvid:
                continue
            out.append(
                VideoItem(
                    video_id=bvid,
                    title=(v.get("title") or "").strip(),
                    url=f"https://www.bilibili.com/video/{bvid}",
                    cover=v.get("pic", "") or "",
                    author=(v.get("author") or "").strip(),
                    published_at=int(v.get("created", 0) or 0),
                    extra={"play": v.get("play", 0)},
                )
            )
        return out

    # ---------- UID 解析 ----------
    def resolve_uid(self, url: str) -> int | None:
        """解析 UID：本地正则优先，b23.tv 短链跟随重定向后解析。"""
        url = (url or "").strip()
        m = re.search(r"space\.bilibili\.com/(\d+)", url)
        if m:
            return int(m.group(1))
        if "b23.tv" in url:
            try:
                r = self._session.get(url, timeout=10, allow_redirects=True)
                m = re.search(r"space\.bilibili\.com/(\d+)", r.url)
                if m:
                    return int(m.group(1))
            except requests.RequestException:
                pass
        m = re.search(r"(?:^|[^0-9])(\d{6,12})(?:[^0-9]|$)", url)
        if m:
            return int(m.group(1))
        return None
