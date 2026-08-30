"""本地打包脚本：PyInstaller 单文件 exe。

要点：
- playwright 使用本地系统 Edge/Chrome 的 channel（无头模式），不下载 chromium；
  但仍需 --collect-all playwright 带上其 driver（node）才能运行。
- 有 UPX 时自动用 UPX 压缩（减少体积）；没有则跳过。
- 产物：dist/RSS_Todo.exe（onefile）。
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

# CI / 无头环境下中文 print 崩（cp1252），统一 UTF-8 输出
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent
APP = "RSS_Todo"

PYI_ARGS = [
    "-F",  # onefile
    "--name", APP,
    # 前端资源
    "--add-data", f"{ROOT / 'web' / 'templates'}{shutil.os.pathsep}{'web/templates'}",
    "--add-data", f"{ROOT / 'web' / 'static'}{shutil.os.pathsep}{'web/static'}",
    # playwright driver（node）必须完整打包
    "--collect-all", "playwright",
    # 懒加载依赖显式补上
    "--hidden-import", "lxml.html",
    # 体积裁剪：不打包的冗余
    "--exclude-module", "pytest",
    "--exclude-module", "IPython",
    "--exclude-module", "PyQt5",
    "--exclude-module", "PySide6",
    "--exclude-module", "tkinter",
    "--exclude-module", "matplotlib",
    "--exclude-module", "PIL",
    "--exclude-module", "numpy",
    "--exclude-module", "scipy",
    "--exclude-module", "PyInstaller",
    # 打包环境自身组件/测试框架/无用标准库（Flask 运行时用不到）
    "--exclude-module", "setuptools",
    "--exclude-module", "pip",
    "--exclude-module", "pkg_resources",
    "--exclude-module", "wheel",
    "--exclude-module", "unittest",
    "--exclude-module", "pydoc",
    "--exclude-module", "doctest",
    "--exclude-module", "distutils",
    "--exclude-module", "test",
    "--disable-windowed-traceback",
    str(ROOT / "app.py"),
]

# UPX 压缩（可选）
upx = shutil.which("upx")
if not upx:
    cand = ROOT / "upx" / "upx.exe"
    if cand.exists():
        upx = str(cand)
if upx:
    print(f"[build] 使用 UPX 压缩: {upx}")
    PYI_ARGS += ["--upx-dir", str(Path(upx).parent)]
else:
    print("[build] 未找到 UPX，跳过压缩（体积会略大）")


def run() -> int:
    print(f"[build] 清理旧产物…")
    for d in ("build", "dist"):
        p = ROOT / d
        if p.exists():
            bak = ROOT / f"{d}_old"
            if bak.exists():
                shutil.rmtree(bak, ignore_errors=True)
            p.rename(bak)  # rename 绕过 PyInstaller safe-delete 问题
    print(f"[build] PyInstaller 参数:\n  {' '.join(PYI_ARGS)}")
    # 必须在 venv/系统 python 下运行（保证 pyinstaller 可见）
    code = subprocess.call([sys.executable, "-m", "PyInstaller", *PYI_ARGS],
                           cwd=str(ROOT))
    if code != 0:
        print("[build] 打包失败")
        return code
    exe = ROOT / "dist" / f"{APP}.exe"
    if not exe.exists():
        print("[build] 未找到产物 exe")
        return 1
    print(f"[build] OK: {exe}（{exe.stat().st_size / 1024 / 1024:.1f} MB）")
    return 0


if __name__ == "__main__":
    sys.exit(run())
