# -*- coding: utf-8 -*-
"""用 Edge 真实浏览器的完整 Cookie 复现 upstat/arc-search，逐个参数组合测试。"""
import json, time, requests, sys
sys.path.insert(0, ".")
from core.adapters.bilibili import UA

MID = 730732
SPACE_HEADERS = {
    "User-Agent": UA,
    "Referer": f"https://space.bilibili.com/{MID}",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Origin": "https://space.bilibili.com",
}

# Edge 无头抓到的完整指纹 Cookie
COOKIES = {
    "buvid3": "6F0175BB-803C-FF67-F51B-45C57D4CBBBC74514infoc",
    "b_nut": "1788070674",
    "buvid4": "DF8EE10A-0D53-4272-D528-C89752A0373B81052-026083014-aEiJ0i5D%2FILiwbdjW2jr8A%3D%3D",
    "buvid_fp": "b4326929b93903ae7510e135cb2beac1",
    "b_lsid": "77B6C27B_1A0515161AB",
}
# bili_ticket 有时效，单独一组
COOKIES_TICKET = dict(COOKIES)
COOKIES_TICKET["bili_ticket"] = ("eyJhbGciOiJIUzI1NiIsImtpZCI6InMwMyIsInR5cCI6IkpXVCJ9."
    "eyJleHAiOjE3ODgzMjk4NzUsImlhdCI6MTc4ODA3MDYxNSwicGx0IjotMX0."
    "uaBnMWFfG_RJx86HhABhxw8eEO-HREku3--VyR0v0KQ")
COOKIES_TICKET["bili_ticket_expires"] = "1788329815"


def ck_str(d):
    return "; ".join(f"{k}={v}" for k, v in d.items())


def try_upstat(label, params, cookies):
    h = dict(SPACE_HEADERS)
    h["Cookie"] = ck_str(cookies)
    try:
        r = requests.get("https://api.bilibili.com/x/space/upstat",
                         params=params, headers=h, timeout=15)
        d = r.json()
        data = d.get("data") or {}
        arch = data.get("archive") or {}
        print(f"[{label}] HTTP={r.status_code} code={d.get('code')} "
              f"msg={d.get('message')} | archive.view={arch.get('view')} "
              f"likes={data.get('likes')} | data={json.dumps(data, ensure_ascii=False)[:80]}")
        return data
    except Exception as e:
        print(f"[{label}] 异常: {e}")
        return None


print("=== upstat 参数组合测试 ===")
base = {"mid": MID}
# 1. 只有 buvid3
try_upstat("1 仅buvid3", base, {"buvid3": COOKIES["buvid3"]})
# 2. buvid3 + buvid4
try_upstat("2 buvid3+4", base, {"buvid3": COOKIES["buvid3"], "buvid4": COOKIES["buvid4"]})
# 3. buvid3+4+fp+lsid
try_upstat("3 全套指纹(无ticket)", base, COOKIES)
# 4. 全套指纹 + bili_ticket
try_upstat("4 全套+ticket", base, COOKIES_TICKET)
# 5. 全套 + web_location
try_upstat("5 全套+ticket+loc", {**base, "web_location": "333.1387"}, COOKIES_TICKET)
# 6. 浏览器完整请求头（含 Origin）+全套
print("\n=== 完整浏览器请求头 ===")
try:
    h = dict(SPACE_HEADERS)
    h["Cookie"] = ck_str(COOKIES_TICKET)
    r = requests.get("https://api.bilibili.com/x/space/upstat",
                     params={"mid": MID, "web_location": "333.1387"},
                     headers=h, timeout=15)
    print("upstat 完整头:", r.status_code, r.json().get("code"), r.json().get("message"),
          "| data=", json.dumps(r.json().get("data"), ensure_ascii=False)[:120])
except Exception as e:
    print("异常:", e)
