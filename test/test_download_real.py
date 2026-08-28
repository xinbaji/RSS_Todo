"""真实环境端到端下载验证：Storage + DownloadManager 对 B 站真实视频做 360p 下载。

验证内容：
  a. video   模式  -> save_dir/<视频名>/ 下存在非空 .mp4
  b. danmaku 模式  -> 生成非空 弹幕.xml
  c. audio   模式  -> 生成非空 .m4a/.mp3
  d. 取消           -> 新任务立即 cancel() 后状态为 canceled

无 Cookie，B 站可能限流；失败时原样打印错误，不伪造结果。
已下载完成的终态任务（同标题）自动复用，避免重复下载。
"""
from __future__ import annotations

import logging
import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

from core.config import Config  # noqa: E402
from core.downloader import DownloadManager, sanitize_filename  # noqa: E402
from core.storage import (  # noqa: E402
    DL_CANCELED, DL_FAILED, DL_RUNNING, DL_SUCCESS, Storage,
)

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                    stream=sys.stdout)

BVID = "BV17E896RE5G"
URL = f"https://www.bilibili.com/video/{BVID}"
ROOT = BASE / ".e2e_dl"
DB_DIR = BASE / ".e2e_storage"

TIMEOUT = 180  # 每个任务等待上限（秒）


def wait_final(storage, dl_id: int, timeout: int = TIMEOUT) -> dict:
    start = time.time()
    while time.time() - start < timeout:
        dl = storage.get_download(dl_id)
        if dl["status"] in (DL_SUCCESS, DL_FAILED, DL_CANCELED):
            return dl
        time.sleep(1)
    return storage.get_download(dl_id)


def size_str(p: Path) -> str:
    n = p.stat().st_size
    return f"{n} B ({n / 1024 / 1024:.2f} MiB)"


def list_folder(folder: Path) -> str:
    if not folder.exists():
        return "(目录不存在)"
    names = sorted(x.name for x in folder.iterdir())
    return ", ".join(names) if names else "(空目录)"


def find_existing(storage, title: str) -> int | None:
    """若已存在终态任务（同标题），直接复用，避免重复下载。"""
    for dl in storage.list_downloads():
        if dl["title"] == title and dl["status"] in (DL_SUCCESS, DL_FAILED, DL_CANCELED):
            return dl["id"]
    return None


def run_mode(storage, mgr, content: str, tag: str):
    title = f"E2E-{tag}-{BVID}"
    item = {"video_id": BVID, "title": title, "url": URL}
    existing = find_existing(storage, title)
    if existing is not None:
        print(f"\n===== [{tag}] 复用既有任务 dl_id={existing} =====")
        dl = storage.get_download(existing)
    else:
        dl_id = mgr.enqueue(item, content, "360p", str(ROOT))
        dl = wait_final(storage, dl_id)
        print(f"\n===== [{tag}] content_type={content} dl_id={dl_id} =====")

    folder = ROOT / sanitize_filename(title)
    print(f"  status      : {dl['status']}  (progress={dl['progress']})")
    print(f"  save_dir    : {ROOT}")
    print(f"  目标文件夹  : {folder}")
    print(f"  文件夹内容  : {list_folder(folder)}")
    if dl.get("error"):
        print(f"  错误原文    : {dl['error']}")
    if dl.get("file_path"):
        print(f"  file_path   : {dl['file_path']}")

    if content == "video":
        files = sorted(folder.glob("*.mp4")) if folder.exists() else []
    elif content == "danmaku":
        files = [folder / "弹幕.xml"] if (folder / "弹幕.xml").exists() else []
    else:  # audio
        files = sorted(list(folder.glob("*.m4a")) + list(folder.glob("*.mp3"))) \
            if folder.exists() else []

    nonempty = [p for p in files if p.is_file() and p.stat().st_size > 0]
    ok = dl["status"] == DL_SUCCESS and nonempty
    if nonempty:
        for p in nonempty:
            print(f"  产出文件    : {p}  {size_str(p)}")
    else:
        print("  产出文件    : 无（未生成目标文件）")

    if dl["status"] == DL_FAILED:
        print(f"  -> 结果: 失败（下载阶段报错，见上方错误原文）")
    elif ok:
        print(f"  -> 结果: 通过")
    elif dl["status"] == DL_SUCCESS:
        print(f"  -> 结果: 失败（status=success 但无目标文件）")
    else:
        print(f"  -> 结果: 失败（status={dl['status']}）")
    return {"ok": ok, "dl": dl, "files": nonempty, "folder": folder}


def run_cancel(storage, mgr) -> dict:
    print(f"\n===== [cancel] 新建任务后立即 cancel() =====")
    last = None
    for attempt in range(3):
        title = f"E2E-cancel-{int(time.time())}-{attempt}"
        item = {"video_id": BVID, "title": title, "url": URL}
        dl_id = mgr.enqueue(item, "video", "360p", str(ROOT))
        cancel_ret = mgr.cancel(dl_id)
        print(f"  attempt={attempt} dl_id={dl_id} cancel()={cancel_ret} "
              f"(enqueue 后即时状态={storage.get_download(dl_id)['status']})")
        dl = wait_final(storage, dl_id, timeout=30)
        print(f"  最终状态: {dl['status']}")
        last = dl
        if dl["status"] == DL_CANCELED:
            print("  -> 结果: 通过")
            return {"ok": True, "dl": dl}
        time.sleep(1)  # 偶发竞态（已 running 但下载过快完成）则重试
    print(f"  -> 结果: 失败（最终 status={last['status']}，error={last.get('error')!r}）")
    return {"ok": False, "dl": last}


def main() -> int:
    ROOT.mkdir(parents=True, exist_ok=True)
    DB_DIR.mkdir(parents=True, exist_ok=True)
    storage = Storage(DB_DIR)
    config = Config(DB_DIR)  # cookie 为空 -> 无 Cookie 请求
    mgr = DownloadManager(storage, config)

    print(f"== rss-todo 下载引擎真实验证 ==")
    print(f"  bvid : {BVID}")
    print(f"  url  : {URL}")
    print(f"  质量 : 360p | Cookie: 无")
    print(f"  下载根: {ROOT} | 数据库: {DB_DIR}")

    results = {}
    results["video"] = run_mode(storage, mgr, "video", "video")
    results["danmaku"] = run_mode(storage, mgr, "danmaku", "danmaku")
    results["audio"] = run_mode(storage, mgr, "audio", "audio")
    results["cancel"] = run_cancel(storage, mgr)

    print("\n========== 汇总 ==========")
    for k, r in results.items():
        tag = "通过" if r["ok"] else "失败"
        detail = r["dl"]["status"]
        if r.get("files"):
            detail += " | " + " ; ".join(f"{p} {size_str(p)}" for p in r["files"])
        elif r["dl"].get("error"):
            detail += " | ERROR: " + r["dl"]["error"]
        print(f"  [{tag}] {k:<8} {detail}")

    storage.close()
    failed = sum(1 for r in results.values() if not r["ok"])
    print(f"\n总览: {4 - failed} 通过 / {failed} 失败")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
