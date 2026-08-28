@echo off
rem rss-todo 一键启动脚本（GBK 编码，适配中文 Windows 默认代码页）
setlocal
cd /d "%~dp0"

set "PY="

rem 1) 优先使用项目内的 venv
if exist "venv\Scripts\python.exe" (
    set "PY=venv\Scripts\python.exe"
)

rem 2) 否则使用 PATH 中的 python
if not defined PY (
    where python >nul 2>nul
    if not errorlevel 1 set "PY=python"
)

rem 3) 都没有则给出提示
if not defined PY (
    echo [错误] 未找到 Python 解释器。
    echo        请先安装 Python 3.9 及以上版本并勾选 "Add Python to PATH"，
    echo        或在项目目录下创建 venv 虚拟环境后重试。
    pause
    exit /b 1
)

echo 使用解释器: %PY%

rem 4) 检查核心依赖是否齐全，缺失则自动安装
"%PY%" -c "import flask, requests, yt_dlp, imageio_ffmpeg, lxml" >nul 2>nul
if errorlevel 1 (
    echo.
    echo [提示] 缺少依赖，正在执行安装：
    echo        "%PY%" -m pip install -r requirements.txt
    echo.
    "%PY%" -m pip install -r requirements.txt
    if errorlevel 1 (
        echo.
        echo [错误] 依赖安装失败，请检查网络后手动执行：
        echo        "%PY%" -m pip install -r requirements.txt
        pause
        exit /b 1
    )
    echo.
    echo [完成] 依赖安装完成。
)

rem 5) 检查可选依赖 playwright（监控 JS 页面用，走系统 Edge/Chrome 无需下载 chromium）
"%PY%" -c "import playwright" >nul 2>nul
if errorlevel 1 (
    echo.
    echo [提示] 未检测到 playwright（监控 JS 页面可选），正在安装：
    echo        "%PY%" -m pip install -r requirements-optional.txt
    echo.
    "%PY%" -m pip install -r requirements-optional.txt
)

rem 6) 启动应用，保持窗口
echo.
echo 正在启动 rss-todo ... 稍后会自动打开浏览器（默认 http://127.0.0.1:8848）
echo 支持附加参数，如：--no-browser 不自动打开浏览器、--port 9000 指定端口
echo 按 Ctrl+C 可停止程序。
echo.
"%PY%" app.py %*

echo.
echo rss-todo 已退出，按任意键关闭窗口。
pause
