"""B 站扫码登录：生成二维码 → 用户扫码 → 轮询获取登录 Cookie → 存入 config。

用户无需手动复制 Cookie；登录状态经 nav 接口查询（头像/昵称/UID）。
"""
from __future__ import annotations

import logging
import time

import requests

from core.adapters.bilibili import NAV_URL, UA

log = logging.getLogger("rss-todo.bili-login")

QR_GEN = "https://passport.bilibili.com/x/passport-login/web/qrcode/generate"
QR_POLL = "https://passport.bilibili.com/x/passport-login/web/qrcode/poll"

# 登录后需要保留的关键 Cookie 字段
WANTED_COOKIES = ("SESSDATA", "bili_jct", "DedeUserID", "DedeUserID__ckMd5", "buvid3")


class QrLogin:
    """维护一次扫码登录会话（必须用同一个 Session 保证 cookie 连续性）。"""

    def __init__(self):
        self._s = requests.Session()
        self._s.headers.update({"User-Agent": UA})

    def generate(self) -> dict:
        """生成二维码，返回 {url, qrcode_key, expires_in}。"""
        resp = self._s.get(QR_GEN, timeout=10)
        data = resp.json()
        if data.get("code") != 0:
            raise RuntimeError(f"生成二维码失败: code={data.get('code')} {data.get('message')}")
        return data["data"]

    def poll(self, qrcode_key: str) -> dict:
        """轮询扫码状态。

        status: pending(未扫) / scanned(已扫待确认) / success(登录成功) / expired(失效)
        success 时附带 cookie 字符串。
        """
        resp = self._s.get(QR_POLL, params={"qrcode_key": qrcode_key}, timeout=10)
        data = resp.json()
        # 顶层 code 恒为 0；业务状态码在 data.code
        code = int((data.get("data") or {}).get("code", data.get("code", -1)))
        if code == 0:
            cookie = self._extract_cookie()
            return {"status": "success", "cookie": cookie,
                    "message": data.get("message", "登录成功")}
        if code == 86038:
            return {"status": "expired", "message": "二维码已失效，请重新生成"}
        if code == 86090:
            return {"status": "scanned", "message": "已扫码，请在手机上确认"}
        return {"status": "pending", "code": code, "message": "等待扫码"}

    def _extract_cookie(self) -> str:
        parts = []
        for c in self._s.cookies:
            if c.name in WANTED_COOKIES and c.value:
                parts.append(f"{c.name}={c.value}")
        return "; ".join(parts)


def account_info(cookie: str) -> dict:
    """用 Cookie 查询登录状态：isLogin / uname / mid / face。"""
    if not cookie:
        return {"isLogin": False}
    headers = {"User-Agent": UA, "Cookie": cookie}
    try:
        data = requests.get(NAV_URL, headers=headers, timeout=10).json()
    except (requests.RequestException, ValueError) as e:
        return {"isLogin": False, "error": str(e)}
    d = data.get("data") or {}
    is_login = bool(d.get("isLogin"))
    if not is_login:
        return {"isLogin": False}
    return {
        "isLogin": True,
        "uname": d.get("uname", ""),
        "mid": d.get("mid"),
        "face": d.get("face", ""),
        "level": d.get("level_info", {}).get("current_level"),
    }
