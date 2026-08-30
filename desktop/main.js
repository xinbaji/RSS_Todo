/* RSS_Todo Electron 壳：启动内嵌后端 exe → 等待端口就绪 → 打开无边框应用窗口 */
const { app, BrowserWindow, dialog, shell } = require("electron");
const { spawn } = require("child_process");
const path = require("path");
const http = require("http");

const log = (...a) => console.log("[rsstodo]", ...a);

// 低资源环境/远程桌面兼容：禁用 GPU 加速（必须在 ready 前）
// disableHardwareAcceleration 是官方 API，比 commandLine 开关更可靠
// （无 GPU 环境若不彻底禁用，GPU 进程反复崩溃会导致窗口加载失败）
app.disableHardwareAcceleration();
app.commandLine.appendSwitch("disable-gpu");
app.commandLine.appendSwitch("disable-gpu-compositing");
// 无 GPU/远程桌面环境实测（最小窗口测试验证）：
//   - 渲染进程被沙箱拉起失败被杀 → --no-sandbox
//   - 独立 GPU 进程反复崩溃 → --in-process-gpu（GPU 线程并入主进程）
app.commandLine.appendSwitch("no-sandbox");
app.commandLine.appendSwitch("disable-gpu-sandbox");
app.commandLine.appendSwitch("in-process-gpu");

const PORT = 8848;
const START_TIMEOUT = 40000;
let backend = null;
let win = null;

function backendExe() {
  // 打包后内嵌在 resources；开发模式用 ../dist/RSS_Todo.exe
  if (app.isPackaged) return path.join(process.resourcesPath, "backend.exe");
  return path.join(__dirname, "..", "dist", "RSS_Todo.exe");
}

function waitBackend(timeoutMs) {
  return new Promise((resolve, reject) => {
    const t0 = Date.now();
    const check = () => {
      const req = http.get(`http://127.0.0.1:${PORT}/api/health`, (res) => {
        res.resume();
        if (res.statusCode === 200) resolve();
        else retry();
      });
      req.on("error", retry);
      req.setTimeout(2000, () => { req.destroy(); retry(); });
    };
    const retry = () => {
      if (Date.now() - t0 > timeoutMs) reject(new Error("后端启动超时"));
      else setTimeout(check, 600);
    };
    check();
  });
}

function startBackend() {
  const exe = backendExe();
  const fs = require("fs");
  if (!fs.existsSync(exe)) {
    throw new Error(`后端可执行文件不存在：\n${exe}\n\n请检查安装包是否完整（resources/backend.exe 应随应用一起安装）。`);
  }
  log("spawn 后端:", exe);
  backend = spawn(exe, ["--no-browser", "--port", String(PORT)], {
    windowsHide: true,
    stdio: "ignore",  // PyInstaller bootloader 在某些环境对 pipe 阻塞，直接忽略子进程 IO
  });
  // 后端异常退出（如缺依赖/被杀软拦截）时立即提示
  backend.on("error", (e) => { log("后端 spawn error:", e.message); });
  backend.on("exit", (code, signal) => {
    log("后端已退出", { code, signal });
    if (win && !win.isDestroyed() && code !== 0 && code != null) {
      dialog.showErrorBox("RSS_Todo 后端异常退出", `后端进程未能正常运行（exit code ${code}）。`);
      app.quit();
    } else if (win && !win.isDestroyed()) {
      app.quit();
    }
  });
}

function shutdownBackend() {
  if (!backend) return;
  try {
    // 优先走 /api/shutdown 优雅退出（停调度/存储），失败再强杀
    const req = http.get(`http://127.0.0.1:${PORT}/api/shutdown`, { method: "POST" });
    req.on("error", () => { try { backend.kill(); } catch (_) {} });
    req.setTimeout(3000, () => { try { backend.kill(); } catch (_) {} });
  } catch (_) {
    try { backend.kill(); } catch (__) {}
  }
}

function createWindow() {
  win = new BrowserWindow({
    width: 1280,
    height: 820,
    autoHideMenuBar: true,
    title: "RSS_Todo",
    backgroundColor: "#12151b",
  });
  // 先显示本地加载页（立即渲染，无黑屏），后端就绪后跳转真实页面
  win.loadFile(path.join(__dirname, "loading.html"));
  // 外链（target=_blank 的视频跳转/监控链接等）一律交给系统默认浏览器
  // （Edge/Chrome）打开，不再开 Electron 内嵌窗口
  win.webContents.setWindowOpenHandler(({ url }) => {
    if (/^https?:\/\//i.test(url)) shell.openExternal(url);
    return { action: "deny" };
  });
  win.on("closed", () => { win = null; });
}

app.whenReady().then(async () => {
  log("ready，启动后端:", backendExe());
  try {
    startBackend();
  } catch (e) {
    log("后端启动前失败:", e.message);
    dialog.showErrorBox("RSS_Todo 启动失败", e.message);
    app.quit();
    return;
  }
  // 5 秒早期检查：spawn error 或后端立即退出 → 直接报错（不等 40 秒）
  setTimeout(() => {
    const earlyErr = backend._earlyError?.();
    if (earlyErr) {
      log("后端 spawn 错误:", earlyErr);
      dialog.showErrorBox("RSS_Todo 启动失败",
        `后端启动失败：${earlyErr}\n\n（窗口 8848 端口被占用也会导致类似问题，请检查）`);
      app.quit();
    }
  }, 5000);
  try {
    await waitBackend(START_TIMEOUT);
    log("后端已就绪");
  } catch (e) {
    log("后端启动失败:", e.message);
    dialog.showErrorBox("RSS_Todo 启动失败",
      `后端服务未能在规定时间内启动，请检查端口 ${PORT} 是否被占用。\n\n${e.message}`);
    app.quit();
    return;
  }
  try {
    createWindow();
    log("窗口已创建");
    // 后端就绪后再加载真实页面（消除黑屏）
    win.loadURL(`http://127.0.0.1:${PORT}`);
  } catch (e) {
    log("窗口创建失败:", e.message);
  }
});

app.on("window-all-closed", () => {
  shutdownBackend();
  app.quit();
});
