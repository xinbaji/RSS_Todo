# -*- coding: utf-8 -*-
"""Edge 无头 v9：抓全部 API 响应，找出含获赞(like_num)/总播放的数据源。"""
import json, time
from playwright.sync_api import sync_playwright

MID = "730732"
URL = f"https://space.bilibili.com/{MID}"


def main():
    with sync_playwright() as p:
        b = p.chromium.launch(
            executable_path=r"C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
            headless=True)
        ctx = b.new_context(locale="zh-CN",
                            viewport={"width": 1400, "height": 900})
        pg = ctx.new_page()
        seen = {}

        def on_response(resp):
            url = resp.url
            if "api.bilibili.com" not in url or resp.request.resource_type != "xhr":
                return
            try:
                body = resp.json()
            except Exception:
                return
            if not isinstance(body, dict):
                return
            data = body.get("data")
            # 记录所有含 archive/likes/play/like_num 的响应
            s = json.dumps(body, ensure_ascii=False)
            has_stat = any(k in s for k in ("like_num", '"likes"', '"view"',
                                            '"play"', "archive", "like/video"))
            if not has_stat:
                return
            key = url.split("?", 1)[0]
            if key in seen:
                return
            seen[key] = body
            print(f"--- {url[:130]}")
            print(f"    {s[:400]}")

        pg.on("response", on_response)
        pg.goto(URL, timeout=45000, wait_until="domcontentloaded")
        time.sleep(8)
        for _ in range(3):
            pg.mouse.wheel(0, 1200)
            time.sleep(1.5)
        time.sleep(2)
        # 模拟 hover 头像区域（可能触发统计数据弹层）
        try:
            pg.hover("css=.h-avatar, .bili-avatar, .space-user-avatar", timeout=5000)
            time.sleep(2)
        except Exception:
            pass
        b.close()


if __name__ == "__main__":
    main()
