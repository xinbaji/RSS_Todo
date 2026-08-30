"""rss-todo 入口：启动 Flask 本地服务 + 用 playwright 打开应用窗口（无地址栏）。"""
from __future__ import annotations

import logging
import os
import sys
import threading
from pathlib import Path

# Windows 打包后控制台默认 cp936，traceback 中文乱码 → 强制 UTF-8
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def _data_base() -> Path:
    """数据目录：frozen（打包后）写到 %LOCALAPPDATA%/RSS_Todo，避免装在 Program Files
    等系统目录时无写权限；源码运行时用脚本所在目录。"""
    if getattr(sys, "frozen", False):
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "RSS_Todo"
    return Path(__file__).resolve().parent


BASE = _data_base()
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

from flask import Flask  # noqa: E402

from core.config import Config  # noqa: E402
from core.downloader import DownloadManager  # noqa: E402
from core.monitor import MonitorChecker, MonitorRules  # noqa: E402
from core.rules import Subscriptions  # noqa: E402
from core.scheduler import Scheduler  # noqa: E402
from core.storage import Storage  # noqa: E402
from core.bilibili_login import QrLogin  # noqa: E402
from web.server import api  # noqa: E402

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                    stream=sys.stdout)


class AppContext:
    """应用共享上下文：存储 / 规则 / 调度 / 下载 / 监控。"""

    def __init__(self, data_dir: Path):
        self.storage = Storage(data_dir)
        self.config = Config(data_dir, storage=self.storage)
        # 下载目录相对路径固定为 data/downloads（exe 从任意目录启动也能定位）
        dl = str(self.config.get("download_dir") or "")
        if not dl or not Path(dl).is_absolute():
            self.config.set("download_dir", str(data_dir / "downloads"))
        self.subs = Subscriptions(storage=self.storage)
        self.monitor_rules = MonitorRules(storage=self.storage)
        self.scheduler = Scheduler(self.storage, self.subs, self.config)
        self.downloader = DownloadManager(self.storage, self.config)
        self.monitor_checker = MonitorChecker(self.monitor_rules, self.storage, self.config)
        self.qr_login = QrLogin()
        self.last_refresh: dict = {}
        # 应用窗口（playwright）：browser/playwright 由窗口打开线程注入
        self.browser = None
        self.playwright = None
        self.scheduler.set_monitor_hook(self.monitor_checker.check)
        # 重启恢复：未完成的下载任务重新入队
        self.downloader.recover_pending()

    def on_refresh_done(self, results: dict) -> None:
        self.last_refresh = results

    def shutdown(self) -> None:
        self.scheduler.stop()
        try:
            self.downloader.stop()
        except Exception:
            pass
        self.storage.close()


def create_app(data_dir: Path | None = None) -> Flask:
    data_dir = data_dir or BASE / "data"
    ctx = AppContext(data_dir)
    app = Flask(__name__, template_folder="web/templates", static_folder="web/static")
    app.extensions["ctx"] = ctx
    app.register_blueprint(api)
    return app


def _cli_port() -> int | None:
    try:
        if "--port" in sys.argv:
            return int(sys.argv[sys.argv.index("--port") + 1])
    except (ValueError, IndexError):
        pass
    return None


def _open_app_window(ctx, url: str) -> None:
    """playwright 打开本地 Edge/Chrome 普通有头窗口（带地址栏），加载页面。

    窗口关闭（点 X）时整个程序退出；启动失败退回系统默认浏览器。
    """
    try:
        from playwright.sync_api import sync_playwright
        p = sync_playwright().start()
        ctx.playwright = p
        browser = p.chromium.launch(
            channel="msedge", headless=False,
            args=["--kiosk",                     # 全屏无地址栏（重点）
                  "--disable-infobars",
                  "--disable-blink-features=AutomationControlled",
                  "--disable-dev-shm-usage",
                  "--no-sandbox",
                  "--window-size=1280,820"])
        ctx.browser = browser
        page = browser.new_page()
        page.goto(url)
        browser.on("disconnected", lambda _b: os._exit(0))
        logging.info("浏览器窗口已打开(kiosk): %s", url)
    except Exception as e:
        logging.warning("浏览器窗口启动失败(%s)，退回系统浏览器：%s", type(e).__name__, e)
        try:
            import webbrowser
            webbrowser.open(url)
        except Exception:
            pass


def _add_file_log(data_dir: Path) -> None:
    """日志写入 data/log/rss_todo.log（文件归类），不影响 stdout 输出。"""
    try:
        log_dir = data_dir / "log"
        log_dir.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(log_dir / "rss_todo.log", encoding="utf-8")
        fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        logging.getLogger().addHandler(fh)
    except Exception:
        pass  # 日志文件不可写时静默降级为仅 stdout


def main() -> None:
    no_browser = "--no-browser" in sys.argv
    app = create_app()
    ctx = app.extensions["ctx"]
    port = _cli_port() or int(ctx.config.get("port", 8848) or 8848)
    _add_file_log(ctx.config.data_dir)
    ctx.scheduler.start()
    if not no_browser:
        threading.Timer(1.2, _open_app_window, args=(ctx, f"http://127.0.0.1:{port}")).start()
    try:
        app.run(host="127.0.0.1", port=port, debug=False, use_reloader=False)
    finally:
        ctx.shutdown()


if __name__ == "__main__":
    main()
