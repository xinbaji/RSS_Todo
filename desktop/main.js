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
  const exe = backendExe();
  const fs = require("fs");
  if (!fs.existsSync(exe)) {
    throw new Error(`后端可执行文件不存在：\n${exe}\n\n请检查安装包是否完整（resources/backend.exe 应随应用一起安装）。`);
  }
  log("spawn 后端:", exe);
  backend = spawn(exe, ["--no-browser", "--port", String(PORT)], {
    windowsHide: true,
  });
  // 收集后端早期错误（spawn 失败 / 后端 5 秒内退出 → 立即 fail-fast，避免等 40 秒）
  let stderrBuf = "";
  let earlyExit = null;
  backend.stderr?.on("data", (c) => { stderrBuf += c.toString(); });
  backend.on("error", (e) => { earlyExit = e.message; });
  backend.on("exit", (code, signal) => {
    log("后端已退出", { code, signal });
    if (win && !win.isDestroyed() && code !== 0 && code != null) {
      const reason = stderrBuf.trim() || `exit code ${code}${signal ? " signal "+signal : ""}`;
      dialog.showErrorBox("RSS_Todo 后端异常退出", `后端进程未能正常运行。\n\n退出信息：${reason}\n\n请把此信息反馈给开发者。`);
      app.quit();
    } else if (win && !win.isDestroyed()) {
      app.quit();
    }
  });
  backend._earlyError = () => earlyExit;
  backend._stderr = () => stderrBuf;
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
    const stderr = backend._stderr?.() || "";
    const detail = stderr ? `\n\n后端输出：\n${stderr.slice(0, 500)}` : "";
    dialog.showErrorBox("RSS_Todo 启动失败",
      `后端服务未能在规定时间内启动，请检查端口 ${PORT} 是否被占用。\n\n${e.message}${detail}`);
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
