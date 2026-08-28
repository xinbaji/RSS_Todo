"""rss-todo 入口：启动 Flask 本地服务 + 自动打开浏览器。"""
from __future__ import annotations

import logging
import sys
import threading
import webbrowser
from pathlib import Path

# exe（PyInstaller onefile）模式下 __file__ 指向解包临时目录，
# 数据目录必须固定在 exe 所在目录，保证重启数据不丢
BASE = (Path(sys.executable).resolve().parent if getattr(sys, "frozen", False)
        else Path(__file__).resolve().parent)
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
        self.config = Config(data_dir)
        # 下载目录相对路径固定为 data/downloads（exe 从任意目录启动也能定位）
        dl = str(self.config.get("download_dir") or "")
        if not dl or not Path(dl).is_absolute():
            self.config.set("download_dir", str(data_dir / "downloads"))
        self.storage = Storage(data_dir)
        self.subs = Subscriptions(data_dir)
        self.monitor_rules = MonitorRules(data_dir)
        self.scheduler = Scheduler(self.storage, self.subs, self.config)
        self.downloader = DownloadManager(self.storage, self.config)
        self.monitor_checker = MonitorChecker(self.monitor_rules, self.storage, self.config)
        self.qr_login = QrLogin()
        self.last_refresh: dict = {}
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


def main() -> None:
    no_browser = "--no-browser" in sys.argv
    app = create_app()
    ctx = app.extensions["ctx"]
    port = _cli_port() or int(ctx.config.get("port", 8848) or 8848)
    ctx.scheduler.start()
    if not no_browser:
        threading.Timer(1.2, lambda: webbrowser.open(f"http://127.0.0.1:{port}")).start()
    try:
        app.run(host="127.0.0.1", port=port, debug=False, use_reloader=False)
    finally:
        ctx.shutdown()


if __name__ == "__main__":
    main()
