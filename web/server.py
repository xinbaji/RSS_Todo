"""Flask API 蓝图：清单 / 订阅 / 下载 / 监控 / 配置。"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

from flask import Blueprint, current_app, jsonify, request

from core.adapters import create_adapter
from core.bilibili_login import account_info
from core.bilibili_stats import inspect as bili_inspect
from core.bilibili_ugc import parse_collection, UGC_SUB_ID
from core.downloader import DownloadManager
from core.monitor import MonitorChecker, MonitorRules, MonitorRuleError
from core.monitor_up import parse_up
from core.rules import RuleError, Subscriptions

api = Blueprint("api", __name__)

# 应用元信息（发版时与 desktop/package.json version、commit vX.Y.Z 同步）
APP_INFO = {
    "name": "RSS_Todo",
    "version": "0.1.7",
    "author": "xinbaji",
    "repo_url": "https://github.com/xinbaji/RSS_Todo",
}


def _ctx():
    """返回注入到 Flask app.extensions["ctx"] 的 AppContext。"""
    return current_app.extensions["ctx"]


# ---------- 应用信息 ----------
@api.get("/api/app-info")
def app_info():
    return jsonify(APP_INFO)


# ---------- 页面 ----------
@api.get("/api/health")
def health():
    return jsonify({"ok": True})


# ---------- 窗口控制（应用窗口模式） ----------
@api.post("/api/window/minimize")
def win_minimize():
    """最小化当前应用窗口（Windows，后台线程执行避免阻塞请求）。"""

    def _min():
        try:
            import ctypes
            hwnd = ctypes.windll.user32.GetForegroundWindow()
            if hwnd:
                ctypes.windll.user32.ShowWindow(hwnd, 6)  # SW_MINIMIZE
        except Exception:
            pass

    threading.Thread(target=_min, daemon=True).start()
    return jsonify({"ok": True})


@api.post("/api/shutdown")
def shutdown():
    """退出程序：先优雅停调度/下载/存储 + 关浏览器窗口，再结束进程。"""
    ctx = _ctx()

    def _do():
        time.sleep(0.4)
        try:
            ctx.shutdown()
        except Exception:
            pass
        try:
            if getattr(ctx, "browser", None):
                ctx.browser.close()
            if getattr(ctx, "playwright", None):
                ctx.playwright.stop()
        except Exception:
            pass
        os._exit(0)

    threading.Thread(target=_do, daemon=True).start()
    return jsonify({"ok": True})


@api.route("/")
def index():
    from flask import render_template
    return render_template("index.html")


# ---------- 待办清单 ----------
@api.get("/api/items")
def items_list():
    status = request.args.get("status", "all")
    sub_id = request.args.get("subscription_id", "") or None
    sub_name = request.args.get("sub_name", "") or None
    if sub_name:
        # 同名订阅合并显示：返回该名字下所有订阅的内容
        ids = [s["id"] for s in _ctx().subs.all() if s["name"] == sub_name]
        if not ids:
            return jsonify({"items": []})
        return jsonify({"items": _ctx().storage.list_items(status, ids)})
    return jsonify({"items": _ctx().storage.list_items(status, sub_id)})


@api.patch("/api/items/<int:item_id>")
def items_patch(item_id):
    body = request.get_json(silent=True) or {}
    status = body.get("status")
    if not status or _ctx().storage.set_status(item_id, status) is False:
        return jsonify({"error": "非法状态或条目不存在"}), 400
    return jsonify({"ok": True, "item": _ctx().storage.get_item(item_id)})


@api.delete("/api/items/<int:item_id>")
def items_delete(item_id):
    if not _ctx().storage.delete_item(item_id):
        return jsonify({"error": "条目不存在"}), 404
    return jsonify({"ok": True})


@api.get("/api/items/stats")
def items_stats():
    return jsonify(_ctx().storage.item_stats())


# ---------- 刷新 ----------
@api.post("/api/refresh")
def refresh_all():
    ctx = _ctx()
    ctx.scheduler.refresh_in_background(on_done=ctx.on_refresh_done)
    return jsonify({"ok": True, "message": "刷新已启动"})


@api.get("/api/refresh/status")
def refresh_status():
    ctx = _ctx()
    return jsonify({
        "running": ctx.scheduler.busy or ctx.monitor_checker.is_busy(),
        "last": ctx.last_refresh,
    })


@api.post("/api/refresh/<sub_id>")
def refresh_one(sub_id):
    ctx = _ctx()
    sub = ctx.subs.get(sub_id)
    if not sub:
        return jsonify({"error": "订阅不存在"}), 404
    if request.args.get("sync") == "1":
        # 同步刷新：立即抓取并返回结果（用于保存订阅后立即按深度抓取）
        res = ctx.scheduler.refresh_subscription(sub)
        return jsonify({"ok": True, **res})
    ctx.scheduler.refresh_in_background(target=sub_id, on_done=ctx.on_refresh_done)
    return jsonify({"ok": True, "message": "刷新已启动"})


# ---------- 订阅 ----------
@api.get("/api/subscriptions")
def subs_list():
    return jsonify({"subscriptions": [
        s for s in _ctx().subs.all()
        if s.get("id") not in ("ugc_import", "monitor_alerts")
    ]})


# ---------- 视频合集导入 ----------
@api.post("/api/ugc/parse")
def ugc_parse():
    """解析 B 站分P合集（不写入），返回预览信息。"""
    body = request.get_json(silent=True) or {}
    url = (body.get("url") or "").strip()
    if not url:
        return jsonify({"error": "缺少 url"}), 400
    cookie = (body.get("cookie") or _ctx().config.get("cookie") or "").strip()
    try:
        info = parse_collection(url, cookie=cookie)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 502
    return jsonify({"collection": info})


@api.post("/api/ugc/import")
def ugc_import():
    """解析 B 站分P合集，每个分P 入一条待办。

    sub_id 可选：默认 ugc_import 合成订阅；保存合集订阅后传真实订阅 id，
    与后续定时刷新共用同一去重键（subscription_id, video_id），不会重复入。
    """
    body = request.get_json(silent=True) or {}
    url = (body.get("url") or "").strip()
    sub_id = body.get("sub_id") or UGC_SUB_ID
    if not url:
        return jsonify({"error": "缺少 url"}), 400
    cookie = (body.get("cookie") or _ctx().config.get("cookie") or "").strip()
    try:
        info = parse_collection(url, cookie=cookie)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 502
    pages = info["pages"]
    if not pages:
        return jsonify({"error": "该视频没有分P", "info": info}), 400
    ctx = _ctx()
    # 仅合成订阅需要自动创建（真实订阅已存在于订阅管理）
    if sub_id == UGC_SUB_ID and not ctx.subs.get(UGC_SUB_ID):
        ctx.subs.add({
            "id": UGC_SUB_ID,
            "name": "合集导入",
            "adapter": "ugc",
            "enabled": True,
            "refresh_interval_minutes": None,
            "config": {"uid": 0, "keywords": [], "match_logic": "all",
                       "fetch_mode": "latest", "fetch_depth": 1},
        })
    imported = skipped = 0
    matched = [info["title"]]  # 命中关键词用合集标题
    for p in pages:
        video = {
            "video_id": f"{info['bvid']}_p{p['page']}",
            "title": f"{info['title']} - P{p['page']} {p['part']}".strip(),
            "url": f"https://www.bilibili.com/video/{info['bvid']}?p={p['page']}",
            "cover": info["pic"],
            "author": info["up_name"],
            "published_at": int(info.get("pubdate", 0) or 0),
        }
        if ctx.storage.add_item(sub_id, video, matched):
            imported += 1
        else:
            skipped += 1
    return jsonify({"ok": True, "imported": imported, "skipped": skipped,
                    "title": info["title"], "bvid": info["bvid"],
                    "total_pages": len(pages)})


@api.post("/api/subscriptions")
def subs_add():
    body = request.get_json(silent=True) or {}
    try:
        sub = _ctx().subs.add(body)
    except RuleError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"ok": True, "subscription": sub})


@api.put("/api/subscriptions/<sub_id>")
def subs_update(sub_id):
    body = request.get_json(silent=True) or {}
    try:
        sub = _ctx().subs.update(sub_id, body)
    except RuleError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"ok": True, "subscription": sub})


@api.delete("/api/subscriptions/<sub_id>")
def subs_delete(sub_id):
    ok = _ctx().subs.remove(sub_id)
    if not ok:
        return jsonify({"error": "订阅不存在"}), 404
    # 级联删除该订阅下所有待办与已见历史
    _ctx().storage.clear_subscription(sub_id)
    return jsonify({"ok": True})


@api.post("/api/subscriptions/parse-uid")
def subs_parse_uid():
    body = request.get_json(silent=True) or {}
    url = (body.get("url") or "").strip()
    if not url:
        return jsonify({"error": "缺少 url"}), 400
    adapter = create_adapter("bilibili", {}, _ctx().config.all())
    uid = adapter.resolve_uid(url)
    if not uid:
        return jsonify({"error": "无法从链接解析 UID"}), 400
    return jsonify({"ok": True, "uid": uid})


# ---------- 监控 UP 解析 ----------
@api.post("/api/monitor/parse-up")
def monitor_parse_up():
    body = request.get_json(silent=True) or {}
    url = (body.get("url") or "").strip()
    if not url:
        return jsonify({"error": "缺少 url"}), 400
    try:
        info = parse_up(url)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    # 尽量带 UP 昵称（acc/info，失败留空）
    from core.monitor_up import fetch_up_name
    info["name"] = fetch_up_name(info["mid"],
                                 cookie=(body.get("cookie") or _ctx().config.get("cookie") or "").strip())
    return jsonify({"ok": True, **info})


@api.post("/api/subscriptions/<sub_id>/reset-history")
def subs_reset_history(sub_id):
    ctx = _ctx()
    if not ctx.subs.get(sub_id):
        return jsonify({"error": "订阅不存在"}), 404
    ctx.storage.reset_history(sub_id)
    return jsonify({"ok": True})


# ---------- 下载 ----------
@api.post("/api/items/<int:item_id>/download")
def item_download(item_id):
    ctx = _ctx()
    item = ctx.storage.get_item(item_id)
    if not item:
        return jsonify({"error": "条目不存在"}), 404
    body = request.get_json(silent=True) or {}
    content_type = body.get("content_type", "video")
    quality = body.get("quality", "best")
    save_dir = (body.get("save_dir") or ctx.config.get("download_dir", "data/downloads"))
    dl_id = ctx.downloader.enqueue(item, content_type, quality, save_dir)
    return jsonify({"ok": True, "download_id": dl_id})


@api.post("/api/downloads/from-url")
def download_from_url():
    """按链接新建下载任务：B 站单视频或合集（分P）批量入队。

    body: {url, content_type, quality, save_dir, pages:[页码...]}
    - pages 为空 → 单视频入队一个任务
    - pages 非空 → 每个勾选分P 入队一个任务，folder_name=专辑标题（同一文件夹）
    """
    ctx = _ctx()
    body = request.get_json(silent=True) or {}
    url = (body.get("url") or "").strip()
    if not url:
        return jsonify({"error": "缺少 url"}), 400
    content_type = body.get("content_type", "video")
    quality = body.get("quality", "best")
    save_dir = (body.get("save_dir")
                or ctx.config.get("download_dir", "data/downloads") or "").strip()
    cookie = ctx.config.get("cookie") or ""
    try:
        info = parse_collection(url, cookie=cookie)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 502
    # 勾选的分P 页码（非法值忽略）
    page_nos = set()
    for p in (body.get("pages") or []):
        try:
            page_nos.add(int(p))
        except (TypeError, ValueError):
            pass
    bvid = info["bvid"]
    ids: list[int] = []
    if page_nos:
        from core.downloader import sanitize_filename
        folder_name = sanitize_filename(info["title"])
        for p in info["pages"]:
            if p.get("page") not in page_nos:
                continue
            item = {
                "video_id": f"{bvid}_p{p['page']}",
                "title": f"{info['title']} - P{p['page']} {p.get('part', '')}".strip(),
                "url": f"https://www.bilibili.com/video/{bvid}?p={p['page']}",
                "cover": info["pic"],
                "author": info["up_name"],
            }
            ids.append(ctx.downloader.enqueue(item, content_type, quality,
                                              save_dir, folder_name=folder_name))
    else:
        item = {
            "video_id": bvid,
            "title": info["title"],
            "url": f"https://www.bilibili.com/video/{bvid}",
            "cover": info["pic"],
            "author": info["up_name"],
        }
        ids.append(ctx.downloader.enqueue(item, content_type, quality, save_dir))
    return jsonify({"ok": True, "download_ids": ids, "count": len(ids),
                    "title": info["title"]})


@api.get("/api/downloads")
def downloads_list():
    return jsonify({"downloads": _ctx().storage.list_downloads()})


@api.post("/api/downloads/<int:dl_id>/cancel")
def download_cancel(dl_id):
    if not _ctx().downloader.cancel(dl_id):
        return jsonify({"error": "任务不存在或不可取消"}), 400
    return jsonify({"ok": True})


@api.post("/api/downloads/<int:dl_id>/resume")
def download_resume(dl_id):
    if not _ctx().downloader.resume(dl_id):
        return jsonify({"error": "任务不存在或不可恢复"}), 400
    return jsonify({"ok": True})


@api.delete("/api/downloads/<int:dl_id>")
def download_delete(dl_id):
    dl = _ctx().storage.get_download(dl_id)
    if dl and dl["status"] in ("running", "pending"):
        _ctx().downloader.cancel(dl_id)
    ok = _ctx().storage.delete_download(dl_id)
    if not ok:
        return jsonify({"error": "任务不存在"}), 404
    return jsonify({"ok": True})


@api.post("/api/downloads/open-dir")
def download_open_dir():
    """按下载任务 id 打开其落盘文件夹（后端读真实路径、转绝对、失败明确报错）。"""
    body = request.get_json(silent=True) or {}
    ids = body.get("ids") or []
    if not ids:
        return jsonify({"error": "未提供任务"}), 400
    try:
        dl = _ctx().storage.get_download(int(ids[0]))
    except (TypeError, ValueError):
        return jsonify({"error": "任务 id 非法"}), 400
    if not dl:
        return jsonify({"error": "任务不存在"}), 404
    from core.downloader import sanitize_filename
    folder = (Path(dl["save_dir"]) / (dl.get("folder_name")
                                      or sanitize_filename(dl["title"]))).resolve()
    if not folder.exists():
        base = Path(dl["save_dir"]).resolve()
        if base.exists():
            _open_folder(str(base))
            return jsonify({"ok": True, "note": "视频目录不存在，已打开下载根目录",
                            "path": str(base)})
        return jsonify({"error": f"目录不存在: {folder}"}), 404
    _open_folder(str(folder))
    return jsonify({"ok": True, "path": str(folder)})


# ---------- 监控 ----------
@api.get("/api/monitor/rules")
def monitor_rules():
    ctx = _ctx()
    values = ctx.storage.monitor_values()
    rules = ctx.monitor_rules.all()
    default_interval = int(ctx.config.get("default_refresh_minutes", 60) or 60)
    for r in rules:
        v = values.get(r["id"], {})
        r["value"] = v.get("value", "")
        r["fetched_at"] = v.get("fetched_at", 0)
        r["last_error"] = v.get("last_error", "")
        # 实际间隔：规则自己的或全局默认（前端直接显示数字）
        r["interval_minutes"] = int(r.get("refresh_interval_minutes") or default_interval)
    return jsonify({"rules": rules})


@api.post("/api/monitor/rules")
def monitor_add():
    body = request.get_json(silent=True) or {}
    try:
        rule = _ctx().monitor_rules.add(body)
    except MonitorRuleError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"ok": True, "rule": rule})


@api.put("/api/monitor/rules/<rule_id>")
def monitor_update(rule_id):
    body = request.get_json(silent=True) or {}
    try:
        rule = _ctx().monitor_rules.update(rule_id, body)
    except MonitorRuleError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"ok": True, "rule": rule})


@api.delete("/api/monitor/rules/<rule_id>")
def monitor_delete(rule_id):
    ok = _ctx().monitor_rules.remove(rule_id)
    if not ok:
        return jsonify({"error": "规则不存在"}), 404
    return jsonify({"ok": True})


@api.post("/api/monitor/refresh")
def monitor_refresh_all():
    ctx = _ctx()
    result = ctx.monitor_checker.refresh_all()
    return jsonify({"ok": True, "results": result})


@api.post("/api/monitor/refresh/<rule_id>")
def monitor_refresh_one(rule_id):
    result = _ctx().monitor_checker.refresh_one(rule_id)
    # 仅"规则不存在"返回 404；抓取失败（如 XPath 未匹配）应 200 + error 字段
    if not result.get("ok") and result.get("error") == "规则不存在":
        return jsonify(result), 404
    return jsonify(result)


# ---------- 配置 ----------
@api.get("/api/config")
def config_get():
    return jsonify(_ctx().config.all())


@api.post("/api/open-download-dir")
def open_download_dir():
    """在系统文件管理器中打开下载目录。"""
    ctx = _ctx()
    dl = str(ctx.config.get("download_dir") or "")
    try:
        d = Path(dl)
        if not d.is_absolute():
            d = ctx.config.data_dir / "downloads"
        d.mkdir(parents=True, exist_ok=True)
        if os.name == "nt":
            os.startfile(str(d))  # Windows 资源管理器
        else:
            subprocess.Popen(["xdg-open", str(d)])
        return jsonify({"ok": True, "path": str(d)})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@api.put("/api/config")
def config_put():
    ctx = _ctx()
    body = request.get_json(silent=True) or {}
    allowed = ("port", "cookie", "default_refresh_minutes", "default_scraper",
               "download_dir")
    changed_port = False
    for k in allowed:
        if k in body:
            old = ctx.config.get(k)
            ctx.config.set(k, body[k])
            if k == "port" and old != body[k]:
                changed_port = True
    ctx.config.save()
    return jsonify({"ok": True, "config": ctx.config.all(),
                    "port_changed": changed_port})


# ---------- B 站扫码登录 ----------
@api.post("/api/bilibili/login/qrcode")
def login_qrcode():
    try:
        data = _ctx().qr_login.generate()
    except Exception as e:
        return jsonify({"error": f"生成二维码失败: {e}"}), 500
    return jsonify({"ok": True, "qrcode_key": data["qrcode_key"], "url": data["url"]})


@api.get("/api/bilibili/login/poll")
def login_poll():
    key = request.args.get("key", "")
    if not key:
        return jsonify({"error": "缺少 qrcode_key"}), 400
    res = _ctx().qr_login.poll(key)
    if res.get("status") == "success" and res.get("cookie"):
        _ctx().config.set("cookie", res["cookie"])
        _ctx().config.save()
        res.pop("cookie", None)  # 不回传完整 Cookie
    return jsonify(res)


@api.get("/api/bilibili/login/account")
def login_account():
    cookie = _ctx().config.get("cookie") or ""
    return jsonify(account_info(cookie))


@api.post("/api/bilibili/login/logout")
def login_logout():
    _ctx().config.set("cookie", "")
    _ctx().config.save()
    return jsonify({"ok": True})


# ---------- B 站智能识别 ----------
@api.post("/api/bilibili/inspect")
def bili_inspect_route():
    body = request.get_json(silent=True) or {}
    url = (body.get("url") or "").strip()
    if not url:
        return jsonify({"error": "缺少 url"}), 400
    cookie = _ctx().config.get("cookie") or ""
    res = bili_inspect(url, cookie=cookie)
    return jsonify({
        "ok": res.ok, "url": res.url, "bvid": res.bvid, "aid": res.aid,
        "title": res.title, "author": res.author,
        "metrics": res.metrics, "errors": res.errors,
    })


# ---------- 开机启动（Windows 注册表 HKCU\...\Run） ----------
_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def _startup_cmd() -> str:
    py = sys.executable
    script = os.path.abspath(sys.argv[0])
    return f'"{py}" "{script}" --no-browser'


@api.get("/api/startup")
def startup_get():
    if sys.platform != "win32":
        return jsonify({"enabled": False, "support": False})
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as k:
            winreg.QueryValueEx(k, "rss-todo")
            return jsonify({"enabled": True, "support": True})
    except FileNotFoundError:
        return jsonify({"enabled": False, "support": True})
    except OSError:
        return jsonify({"enabled": False, "support": False})


@api.put("/api/startup")
def startup_put():
    if sys.platform != "win32":
        return jsonify({"error": "当前系统不支持开机启动"}), 400
    body = request.get_json(silent=True) or {}
    enabled = bool(body.get("enabled"))
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0,
                            winreg.KEY_SET_VALUE) as k:
            if enabled:
                winreg.SetValueEx(k, "rss-todo", 0, winreg.REG_SZ, _startup_cmd())
            else:
                try:
                    winreg.DeleteValue(k, "rss-todo")
                except FileNotFoundError:
                    pass
        return jsonify({"ok": True, "enabled": enabled})
    except OSError as e:
        return jsonify({"error": f"写入注册表失败: {e}"}), 500


def _open_folder(path: str) -> None:
    """打开本地文件夹（跨平台）。"""
    try:
        if sys.platform == "win32":
            os.startfile(path)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
    except Exception:
        pass
