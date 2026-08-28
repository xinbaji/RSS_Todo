# RSS_Todo

B 站追番追更 + 数据监控的本地 Web 应用。订阅 UP 主视频/分P 合集，自动抓新内容进待办清单；监控 UP 直播与数据指标；支持一键下载、扫码登录、本地单文件部署。

## 功能

- **订阅管理**
  - UP 订阅：粘贴 UP 主页链接/UID，按关键词匹配新视频，刷新自动入清单
  - 合集订阅：粘贴视频链接，自动订阅全部分P，更新到新集时自动入清单
  - 昵称自动识别（刷新后显示 UP 名）、同名订阅合并、删除订阅级联清理待办
- **待办清单**
  - 按订阅分类筛选（全部 / 各订阅）、状态筛选（待办 / 已完成 / 已忽略 / 全部）
  - 全局去重：同一视频无论来自哪个订阅，只出现一次
  - 语义：删除 = 彻底移除（下次刷新会重新加入）；忽略 = 保留不打扰
- **视频下载**：内置 yt-dlp + ffmpeg，一键下载待办视频，进度可视化
- **监控数据**（独立页签，不入待办）
  - **UP 监控**：直播间是否开播（开播高亮可点击进直播间）、关注数、粉丝数、播放量、获赞数变化
  - **B 站视频监控**：播放 / 点赞 / 投币 / 收藏 / 转发 / 弹幕 / 评论数变化
  - 多选指标，变化合并为一张大卡片（大数字 + 增量）；页面监控用 playwright 复用本机 Edge/Chrome 无头抓取
- **B 站登录**：扫码登录，自动保存 cookie（限流与风控友好）

## 快速开始（源码运行）

```bash
pip install -r requirements.txt
python app.py          # 自动打开 http://127.0.0.1:8848
# 或指定端口 / 不开浏览器：
python app.py --port 9000 --no-browser
```

> playwright 使用本机 Edge/Chrome 的 channel（无头模式），**不需要** `playwright install chromium`。
> 未安装 playwright 时，仅"页面 XPath 监控（playwright 抓取）"不可用，其余功能正常。

## 打包单文件 exe（本地）

```bash
pip install -r requirements.txt pyinstaller
python build_exe.py     # 产物 dist/RSS_Todo.exe
```

- PyInstaller onefile；`--collect-all playwright` 内置 driver（仍复用系统 Edge/Chrome，不下载浏览器）
- 自动裁剪无用的重型库（tkinter/PIL/numpy 等）；检测到 UPX 时自动压缩
- 冒烟验证：`dist/RSS_Todo.exe --no-browser --port 8848` 后访问 `http://127.0.0.1:8848`

## GitHub 自动构建发版

推送 commit message 含版本号（如 `feat: xxx v1.0.0`）到 `main`，触发 `.github/workflows/release.yml`：

- windows-latest 构建 → 冒烟测试 exe → 发布 Release（`RSS_Todo-v1.0.0.exe`）

## 配置与数据

运行数据保存在 `data/`（首次运行自动创建）：

| 文件 | 说明 |
|---|---|
| `config.json` | 端口、cookie（登录后自动写入）等 |
| `subscriptions.json` | 订阅规则 |
| `monitor.json` | 监控规则 |
| `app.db` | 待办清单 / 已见历史 / 下载任务 / 监控值 |

> `data/` 已被 `.gitignore` 忽略（含敏感 cookie），请勿提交。

## 目录结构

```
app.py                 # 入口（Flask + 调度器 + 自动开浏览器）
core/
  adapters/            # bilibili / ugc(分P合集) 抓取适配器
  bilibili_*.py        # 登录 / 视频统计 / 合集解析
  scheduler.py         # 定时刷新（间隔可配）
  matcher.py           # 关键词匹配
  monitor.py           # 监控引擎（xpath / bilibili_stat）
  monitor_up.py        # UP 监控（直播 / 粉丝 / 播放 / 获赞）
  storage.py           # SQLite 存储
  downloader.py        # yt-dlp 下载
web/                   # Flask 蓝图 + 前端（原生 JS）
build_exe.py           # PyInstaller 打包脚本
```

## License

MIT
