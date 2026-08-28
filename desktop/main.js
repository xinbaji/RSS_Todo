/* RSS_Todo Electron 壳：启动内嵌后端 exe → 等待端口就绪 → 打开无边框应用窗口 */
const { app, BrowserWindow, dialog } = require("electron");
const { spawn } = require("child_process");
const path = require("path");
const http = require("http");

const log = (...a) => console.log("[rsstodo]", ...a);

// 低资源环境/远程桌面兼容：禁用 GPU 加速
app.commandLine.appendSwitch("disable-gpu");
app.commandLine.appendSwitch("disable-gpu-compositing");

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
  backend = spawn(backendExe(), ["--no-browser", "--port", String(PORT)], {
    windowsHide: true,
  });
  backend.on("exit", () => {
    if (win && !win.isDestroyed()) {
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
  win.loadURL(`http://127.0.0.1:${PORT}`);
  win.on("closed", () => { win = null; });
}

app.whenReady().then(async () => {
  log("ready，启动后端:", backendExe());
  startBackend();
  try {
    await waitBackend(START_TIMEOUT);
    log("后端已就绪");
  } catch (e) {
    log("后端启动失败:", e.message);
    dialog.showErrorBox("RSS_Todo 启动失败",
      "后端服务未能在规定时间内启动，请检查端口 8848 是否被占用。\n\n" + e.message);
    app.quit();
    return;
  }
  try {
    createWindow();
    log("窗口已创建");
  } catch (e) {
    log("窗口创建失败:", e.message);
  }
});

app.on("window-all-closed", () => {
  shutdownBackend();
  app.quit();
});
