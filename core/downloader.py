"""下载引擎：yt-dlp 串行队列，按视频名建文件夹，支持视频/仅音频/弹幕。

- ffmpeg 由 imageio-ffmpeg 附带二进制提供，免手动安装
- 清晰度映射 yt-dlp format；Cookie 复用全局配置
- 弹幕走 B 站官方接口（xml）；暂停/停止统一为中断（可重新入队）
"""
from __future__ import annotations

import logging
import re
import threading
import time
from pathlib import Path

import requests
# yt_dlp / imageio_ffmpeg 为重型依赖，改为下载时才导入，加快启动速度

from .storage import (DL_CANCELED, DL_FAILED, DL_PENDING, DL_RUNNING, DL_SUCCESS)

log = logging.getLogger("rss-todo.downloader")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

QUALITY_FORMAT = {
    "best": "bv*+ba/b",
    "1080p": "bv*[height<=1080]+ba/b[height<=1080]/b[height<=1080]",
    "720p": "bv*[height<=720]+ba/b[height<=720]/b[height<=720]",
    "480p": "bv*[height<=480]+ba/b[height<=480]/b[height<=480]",
    "360p": "bv*[height<=360]+ba/b[height<=360]/b[height<=360]",
}

VALID_CONTENT = ("video", "audio", "danmaku")
CONTENT_PARTS = ("video", "audio", "danmaku")


def split_contents(content_type: str) -> list[str]:
    """把存储的 content_type 拆成类型列表；兼容旧值 both/单类型。"""
    raw = (content_type or "video").lower()
    if raw == "both":  # 旧数据兼容：both = 视频+弹幕
        return ["video", "danmaku"]
    parts = []
    for p in raw.replace(",", "+").split("+"):
        p = p.strip()
        if p in CONTENT_PARTS and p not in parts:
            parts.append(p)
    return parts


class DownloadCanceled(Exception):
    pass


def sanitize_filename(name: str, max_len: int = 120) -> str:
    name = re.sub(r'[\\/:*?"<>|\r\n]+', "_", name or "").strip()
    name = re.sub(r"\s+", " ", name)
    return name[:max_len] or "video"


class DownloadManager:
    def __init__(self, storage, config):
        self.storage = storage
        self.config = config
        self._queue: list[int] = []
        self._cancel: set[int] = set()
        self._worker: threading.Thread | None = None
        self._lock = threading.Lock()

    # ---------- 队列 ----------
    def enqueue(self, item: dict, content_type: str, quality: str,
                save_dir: str) -> int:
        """content_type 支持组合，如 "video+danmaku" / "audio" / "video+audio+danmaku"。"""
        contents = split_contents(content_type)
        if not contents:
            contents = ["video"]
        content_type = "+".join(contents)
        if quality not in QUALITY_FORMAT:
            quality = "best"
        dl_id = self.storage.add_download(item, content_type, quality, save_dir)
        with self._lock:
            self._queue.append(dl_id)
        self._ensure_worker()
        return dl_id

    def _ensure_worker(self) -> None:
        with self._lock:
            if self._worker and self._worker.is_alive():
                return
            self._worker = threading.Thread(target=self._run, daemon=True,
                                            name="rss-downloader")
            self._worker.start()

    def _run(self) -> None:
        while True:
            with self._lock:
                if not self._queue:
                    break
                dl_id = self._queue.pop(0)
            self._execute(dl_id)

    def cancel(self, dl_id: int) -> bool:
        """停止/暂停语义：中断任务；队列中未开始的直接置 canceled。"""
        dl = self.storage.get_download(dl_id)
        if not dl:
            return False
        if dl["status"] == DL_RUNNING:
            self._cancel.add(dl_id)
            return True
        if dl["status"] == DL_PENDING:
            with self._lock:
                if dl_id in self._queue:
                    self._queue.remove(dl_id)
            self.storage.update_download(dl_id, status=DL_CANCELED, finished_at=int(time.time()))
            return True
        return False

    def resume(self, dl_id: int) -> bool:
        """重新开始：canceled/failed 任务重新入队。"""
        dl = self.storage.get_download(dl_id)
        if not dl or dl["status"] not in (DL_CANCELED, DL_FAILED):
            return False
        self.storage.update_download(dl_id, status=DL_PENDING, progress=0, error="")
        with self._lock:
            self._queue.append(dl_id)
        self._ensure_worker()
        return True

    def stop_all(self) -> None:
        for dl in self.storage.list_downloads():
            if dl["status"] in (DL_RUNNING, DL_PENDING):
                self.cancel(dl["id"])

    def stop(self) -> None:
        """停止 worker：取消队列与运行中任务，等待线程退出（shutdown 前调用）。"""
        with self._lock:
            for dl_id in list(self._queue):
                self._queue.remove(dl_id)
                self.storage.update_download(dl_id, status=DL_CANCELED,
                                             finished_at=int(time.time()))
            for dl in self.storage.list_downloads():
                if dl["status"] == DL_RUNNING:
                    self._cancel.add(dl["id"])
        if self._worker and self._worker.is_alive():
            self._worker.join(timeout=8)

    def recover_pending(self) -> None:
        """启动恢复：把上次进程退出时卡住的 running/pending 任务重置为 pending 并重新入队。"""
        recovered = 0
        with self._lock:
            for dl in self.storage.list_downloads():
                if dl["status"] in (DL_RUNNING, DL_PENDING):
                    self.storage.update_download(dl["id"], status=DL_PENDING,
                                                 progress=0, error="")
                    self._queue.append(dl["id"])
                    recovered += 1
        if recovered:
            log.info("恢复 %d 个未完成下载任务", recovered)
            self._ensure_worker()

    # ---------- 执行 ----------
    def _execute(self, dl_id: int) -> None:
        dl = self.storage.get_download(dl_id)
        if not dl or dl["status"] == DL_CANCELED:
            return
        self.storage.update_download(dl_id, status=DL_RUNNING,
                                     started_at=int(time.time()), progress=0)
        try:
            folder = Path(dl["save_dir"]) / sanitize_filename(dl["title"])
            folder.mkdir(parents=True, exist_ok=True)
            for content in split_contents(dl["content_type"]):
                if dl_id in self._cancel:
                    raise DownloadCanceled()
                if content == "video":
                    self._download_video(dl, folder, "video")
                elif content == "audio":
                    self._download_video(dl, folder, "audio")
                elif content == "danmaku":
                    self._download_danmaku(dl, folder)
            if dl_id in self._cancel:
                raise DownloadCanceled()
            self.storage.update_download(dl_id, status=DL_SUCCESS,
                                         progress=100, finished_at=int(time.time()))
        except DownloadCanceled:
            self.storage.update_download(dl_id, status=DL_CANCELED,
                                         finished_at=int(time.time()))
        except Exception as e:
            log.error("下载失败 #%s %s: %s", dl_id, dl.get("title"), e)
            self.storage.update_download(dl_id, status=DL_FAILED, error=str(e)[:500],
                                         finished_at=int(time.time()))
        finally:
            self._cancel.discard(dl_id)

    def _build_opts(self, dl: dict, folder: Path, hook, mode: str = "video") -> dict:
        from imageio_ffmpeg import get_ffmpeg_exe  # 惰性导入
        opts = {
            "format": QUALITY_FORMAT.get(dl["quality"], "bv*+ba/b"),
            "outtmpl": str(folder / "%(title)s.%(ext)s"),
            "ffmpeg_location": get_ffmpeg_exe(),
            "progress_hooks": [hook],
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "restrictfilenames": False,
            "http_headers": {"User-Agent": UA, "Referer": "https://www.bilibili.com/"},
        }
        if mode == "audio":
            opts["format"] = "ba/b"
            opts["postprocessors"] = [{
                "key": "FFmpegExtractAudio", "preferredcodec": "m4a",
                "preferredquality": "0",
            }]
        cookie = (self.config.get("cookie") or "").strip()
        if cookie:
            opts["http_headers"]["Cookie"] = cookie
        return opts

    def _download_video(self, dl: dict, folder: Path, mode: str = "video") -> None:
        def hook(d):
            if d["status"] == "downloading":
                total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                got = d.get("downloaded_bytes", 0)
                pct = got / total * 100 if total else 0
                self.storage.update_download(dl["id"], progress=round(pct, 1))
            elif d["status"] == "finished":
                self.storage.update_download(dl["id"], progress=99,
                                             file_path=str(Path(d.get("filename", ""))))
            if dl["id"] in self._cancel:
                raise DownloadCanceled()

        opts = self._build_opts(dl, folder, hook, mode)
        import yt_dlp  # 惰性导入（重型依赖）
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([dl["url"]])

    def _download_danmaku(self, dl: dict, folder: Path) -> None:
        bvid = dl["video_id"]
        hdrs = {"User-Agent": UA, "Referer": "https://www.bilibili.com/"}
        if (self.config.get("cookie") or "").strip():
            hdrs["Cookie"] = self.config.get("cookie")
        resp = requests.get(f"https://api.bilibili.com/x/player/pagelist?bvid={bvid}",
                            headers=hdrs, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != 0 or not data.get("data"):
            raise RuntimeError(f"获取弹幕 cid 失败: {data.get('message')}")
        cid = data["data"][0]["cid"]
        dm = requests.get(f"https://api.bilibili.com/x/v1/dm/list.so?oid={cid}",
                          headers=hdrs, timeout=15)
        dm.raise_for_status()
        if dl["id"] in self._cancel:
            raise DownloadCanceled()
        path = folder / "弹幕.xml"
        path.write_bytes(dm.content)
        self.storage.update_download(dl["id"], file_path=str(path))
