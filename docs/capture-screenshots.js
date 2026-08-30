/* 截图脚本：加载本地应用页面，依次切换页签截图（供 README 使用）
 *
 * 用法：
 *   1) 启动后端：cd <项目根> && python app.py --no-browser --port 8848
 *   2) 跑脚本：  cd desktop && env -u NODE_OPTIONS -u ELECTRON_RUN_AS_NODE \
 *                  node node_modules/electron/cli.js ../docs/capture-screenshots.js
 *   3) 产物：    docs/screenshots/01-todo.png 等 5 张
 */
const { app, BrowserWindow } = require("electron");
const fs = require("fs");
const path = require("path");

// 无 GPU 环境兼容（与 main.js 一致的开关组合）
app.disableHardwareAcceleration();
app.commandLine.appendSwitch("no-sandbox");
app.commandLine.appendSwitch("disable-gpu-sandbox");
app.commandLine.appendSwitch("in-process-gpu");
app.commandLine.appendSwitch("disable-gpu");
app.commandLine.appendSwitch("disable-gpu-compositing");

// 输出到脚本同级的 screenshots/ 子目录
const OUT = require("path").join(__dirname, "screenshots");
const URL = "http://127.0.0.1:8848";
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

app.whenReady().then(async () => {
  const win = new BrowserWindow({
    width: 1280,
    height: 820,
    show: true,
    backgroundColor: "#12151b",
    webPreferences: { offscreen: false },
  });
  win.webContents.setWindowOpenHandler(() => ({ action: "deny" }));
  await win.loadURL(URL);
  await sleep(4500); // 首屏数据渲染
  fs.mkdirSync(OUT, { recursive: true });

  const shots = [
    { page: "todo", wait: 1500, name: "01-todo" },
    { page: "monitor", wait: 2500, name: "02-monitor" },
    { page: "download", wait: 2000, name: "03-download" },
    { page: "subs", wait: 1800, name: "04-subs" },
    { page: "settings", wait: 1500, name: "05-settings" },
  ];

  for (const s of shots) {
    await win.webContents.executeJavaScript(`
      (() => {
        const tab = document.querySelector('.tab[data-page="${s.page}"]');
        if (tab) tab.click();
        return !!tab;
      })()
    `);
    await sleep(s.wait);
    const img = await win.webContents.capturePage();
    fs.writeFileSync(path.join(OUT, s.name + ".png"), img.toPNG());
    console.log("saved:", s.name + ".png");
  }
  console.log("ALL DONE");
  app.exit(0);
}).catch((e) => {
  console.error("FATAL:", e.message);
  app.exit(1);
});
