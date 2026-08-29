/* rss-todo 前端逻辑：清单 / 监控 / 下载 / 订阅 / 设置 */
"use strict";

/* ============ 工具 ============ */
const $ = (id) => document.getElementById(id);

async function api(path, opts = {}) {
  const resp = await fetch(path, {
    method: opts.method || "GET",
    headers: { "Content-Type": "application/json" },
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) throw new Error(data.error || `请求失败 ${resp.status}`);
  return data;
}

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function fmtTime(ts) {
  if (!ts) return "";
  const d = new Date(ts * 1000);
  const diff = (Date.now() - d.getTime()) / 1000;
  if (diff < 3600) return `${Math.max(1, Math.floor(diff / 60))} 分钟前`;
  if (diff < 86400) return `${Math.floor(diff / 3600)} 小时前`;
  if (diff < 172800) return "昨天 " + d.toTimeString().slice(0, 5);
  const m = d.getMonth() + 1, day = d.getDate();
  return `${m}月${day}日`;
}

function dayGroup(ts) {
  if (!ts) return "更早";
  const d = new Date(ts * 1000);
  const now = new Date();
  const s = (t) => new Date(t.getFullYear(), t.getMonth(), t.getDate()).getTime();
  const diff = (s(now) - s(d)) / 86400000;
  if (diff <= 0) return "今天";
  if (diff === 1) return "昨天";
  return "更早";
}

function sanitizeName(s) {
  return String(s || "").replace(/[\\/:*?"<>|\r\n]+/g, "_").replace(/\s+/g, " ").slice(0, 120) || "video";
}

let toastTimer;
function toast(title, desc) {
  $("toastTitle").textContent = title;
  $("toastDesc").textContent = desc || "";
  $("toastIco").innerHTML = ICON_OK;
  const t = $("toast");
  t.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove("show"), 2600);
}

/* ============ 图标 ============ */
const ICON_JUMP = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M7 17L17 7M9 7h8v8"/></svg>';
const ICON_DL = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3v12m0 0l-4-4m4 4l4-4M4 21h16"/></svg>';
const ICON_OK = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6L9 17l-5-5"/></svg>';
const ICON_DEL = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18M8 6V4a1 1 0 011-1h6a1 1 0 011 1v2m3 0v14a2 2 0 01-2 2H7a2 2 0 01-2-2V6"/></svg>';
const ICON_IGNORE = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10.733 5.076a10.744 10.744 0 0 1 11.205 6.575 1 1 0 0 1 0 .696 10.747 10.747 0 0 1-1.444 2.49"/><path d="M14.084 14.158a3 3 0 0 1-4.242-4.242"/><path d="M17.479 17.499a10.75 10.75 0 0 1-15.417-5.151 1 1 0 0 1 0-.696 10.75 10.75 0 0 1 4.446-5.143"/><path d="m2 2 20 20"/></svg>';
const ICON_RESTORE = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M2 12s3-7 10-7 10 7 10 7-3 7-10 7-10-7-10-7Z"/><circle cx="12" cy="12" r="3"/></svg>';
const ICON_REFRESH = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12a9 9 0 1 1-2.64-6.36M21 3v6h-6"/></svg>';
const ICON_EDIT = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17 3a2.83 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5L17 3z"/></svg>';

/* ============ Tab ============ */
$("tabs").addEventListener("click", (e) => {
  const tab = e.target.closest(".tab");
  if (!tab) return;
  document.querySelectorAll(".tab").forEach((t) => t.classList.remove("active"));
  document.querySelectorAll(".page").forEach((p) => p.classList.remove("active"));
  tab.classList.add("active");
  $("page-" + tab.dataset.page).classList.add("active");
  if (tab.dataset.page === "monitor") loadMonitors();
  if (tab.dataset.page === "download") loadDownloads();
  if (tab.dataset.page === "subs") loadSubs();
  if (tab.dataset.page === "settings") loadSettings();
  if (tab.dataset.page === "todo") { loadSubTabs(); loadItems(); }
});

/* ============ 待办清单 ============ */
let currentFilter = localStorage.getItem("rsstodo.filter") || "todo";
let currentSubFilter = localStorage.getItem("rsstodo.subfilter") || "";
let lastTodoCount = null;

function cardHTML(it) {
  const btn = it.status === "todo"
    ? `<button class="icon-btn done-btn" title="标记已完成" onclick="setStatus(${it.id},'done')">${ICON_OK}</button>`
    : `<button class="icon-btn marked" title="还原为待办" onclick="setStatus(${it.id},'todo')">${ICON_OK}</button>`;
  const ignoreBtn = it.status === "ignored"
    ? `<button class="icon-btn rst-btn" title="还原为待办" onclick="setStatus(${it.id},'todo')">${ICON_RESTORE}</button>`
    : `<button class="icon-btn ign-btn" title="忽略" onclick="setStatus(${it.id},'ignored')">${ICON_IGNORE}</button>`;
  const cover = it.cover
    ? `<img src="${esc(it.cover)}" referrerpolicy="no-referrer" loading="lazy" onerror="this.style.display='none'">`
    : `<div class="fb">${esc(it.title[0] || "?")}</div>`;
  return `<div class="card ${it.status === "ignored" ? "ignored" : ""} ${it.status === "done" ? "done" : ""}">
    <div class="cover">${cover}</div>
    <div class="card-body">
      <div class="card-title">${esc(it.title)}</div>
      <div class="card-meta"><span class="up">${esc(it.author)}</span>
        <span>${fmtTime(it.published_at)}</span>
        ${subName(it.subscription_id) ? `<span class="tag sub-tag">${esc(subName(it.subscription_id))}</span>` : ""}
        ${(it.matched_keywords || []).filter((k) => typeof k === "string").map((k) => `<span class="tag">${esc(k)}</span>`).join("")}
      </div>
      ${(it.matched_keywords || []).some((k) => typeof k === "object" && k.label)
        ? `<div class="up-metrics">${it.matched_keywords.filter((k) => typeof k === "object" && k.label).map((m) =>
            `<div class="up-metric"><div class="v">${esc(m.value)}</div><div class="l">${esc(m.label)} <i>${esc(m.delta || "")}</i></div></div>`).join("")}</div>`
        : ""}
      <div class="card-foot">
        <a class="icon-btn" title="跳转视频页" href="${esc(it.url)}" target="_blank" rel="noopener">${ICON_JUMP}</a>
        <button class="icon-btn dl-btn" title="下载" onclick="openDlModal(${it.id})">${ICON_DL}</button>
        ${btn}
        ${ignoreBtn}
        <button class="icon-btn del-btn" title="删除" onclick="deleteItem(${it.id})">${ICON_DEL}</button>
      </div>
    </div>
  </div>`;
}

async function loadItems() {
  try {
    const qs = `status=${currentFilter}` + (currentSubFilter ? `&sub_name=${encodeURIComponent(currentSubFilter)}` : "");
    const data = await api(`/api/items?${qs}`);
    const list = data.items || [];
    const groups = {};
    list.forEach((i) => { const g = dayGroup(i.published_at); (groups[g] = groups[g] || []).push(i); });
    const order = ["今天", "昨天", "更早"];
    $("todo-groups").innerHTML = order
      .filter((g) => groups[g])
      .map((g) => `<div class="day-group"><div class="day-head">${g}</div>${groups[g].map(cardHTML).join("")}</div>`)
      .join("") || `<div class="empty">暂无内容</div>`;
  } catch (e) {
    $("todo-groups").innerHTML = `<div class="empty">加载失败：${esc(e.message)}</div>`;
  }
}

async function loadStats() {
  try {
    const s = await api("/api/items/stats");
    $("badge-todo").textContent = s.todo || 0;
    $("n-todo").textContent = s.todo || 0;
    $("n-done").textContent = s.done || 0;
    $("n-ignored").textContent = s.ignored || 0;
    $("n-all").textContent = s.total || 0;
    document.title = (s.todo ? `(${s.todo}) ` : "") + "rss-todo";
    if (lastTodoCount !== null && s.todo > lastTodoCount) {
      notify("发现新视频", `待办清单新增 ${s.todo - lastTodoCount} 个条目`);
    }
    lastTodoCount = s.todo;
  } catch (e) { /* 忽略 */ }
}

async function setStatus(id, status) {
  try {
    await api(`/api/items/${id}`, { method: "PATCH", body: { status } });
    await Promise.all([loadItems(), loadStats()]);
  } catch (e) { toast("操作失败", e.message); }
}

async function deleteItem(id) {
  if (!confirm("删除该条目？下次刷新时会重新加入（忽略才不打扰）。")) return;
  try {
    await api(`/api/items/${id}`, { method: "DELETE" });
    await Promise.all([loadItems(), loadStats()]);
    toast("已删除", "条目已从清单移除");
  } catch (e) { toast("删除失败", e.message); }
}

$("filterBar").addEventListener("click", (e) => {
  const b = e.target.closest(".fbtn");
  if (!b) return;
  document.querySelectorAll(".fbtn").forEach((x) => x.classList.remove("active"));
  b.classList.add("active");
  currentFilter = b.dataset.f;
  localStorage.setItem("rsstodo.filter", currentFilter);
  loadItems();
});

/* 订阅分类选项卡：点击显示该订阅的全部内容 */
let subTabsCache = {};

function subName(sid) {
  const s = subTabsCache[sid];
  return s ? s.name : "";
}

async function loadSubTabs() {
  try {
    const { subscriptions } = await api("/api/subscriptions");
    subTabsCache = {};
    (subscriptions || []).forEach((s) => { subTabsCache[s.id] = s; });
    // 同名订阅合并为一个选项卡
    const seen = {};
    const tabs = (subscriptions || []).filter((s) => {
      if (seen[s.name]) return false;
      seen[s.name] = true;
      return true;
    }).map((s) =>
      `<button class="fbtn${currentSubFilter === s.name ? " active" : ""}" data-name="${esc(s.name)}">${esc(s.name)}</button>`);
    $("subFilterBar").innerHTML =
      `<button class="fbtn${currentSubFilter === "" ? " active" : ""}" data-name="">全部订阅</button>` + tabs.join("");
  } catch (e) { /* 忽略 */ }
}

$("subFilterBar").addEventListener("click", (e) => {
  const b = e.target.closest(".fbtn");
  if (!b) return;
  document.querySelectorAll("#subFilterBar .fbtn").forEach((x) => x.classList.remove("active"));
  b.classList.add("active");
  currentSubFilter = b.dataset.name || "";
  localStorage.setItem("rsstodo.subfilter", currentSubFilter);
  loadItems();
});

/* ============ 监控 ============ */
let monEditingTags = [];
const PALETTE = ["#2f6fed", "#e64e7d", "#16a34a", "#b45309", "#7c3aed", "#0d9488", "#dc2626", "#57534e"];
let tagColor = PALETTE[0];

function mtag(t) {
  return `<span class="mtag" style="background:${t.c}1f;color:${t.c};border-color:${t.c}55">${esc(t.t)}</span>`;
}

let monFilter = "all";

function monBadge(cfg) {
  if (cfg.type === "up") return `<span class="mtag" style="background:#fdf0f4;color:#d4537e;border-color:#f4c0d1">UP 监控</span>`;
  if (cfg.type === "bilibili_stat") return `<span class="mtag metric">B 站视频</span>`;
  if (cfg.type === "github_repo") return `<span class="mtag" style="background:#eef0f6;color:#24292f;border-color:#d0d7de">GitHub 仓库</span>`;
  return `<span class="mtag metric">页面</span>`;
}

function monCardHTML(r) {
  const cfg = r.config || {};
  let img = "";
  let ghIcon = false;
  let body = "";
  let nameHTML = esc(r.name);
  let headExtra = "";
  let subHTML = "";
  if (!r.last_error && r.value) {
    try {
      const d = JSON.parse(r.value);
      if (d && d.kind) {
        img = d.kind === "up" ? d.face : d.kind === "stat" ? d.pic : "";
        if (d.kind === "up" && d.mid) {
          nameHTML = `<a class="up-link" href="https://space.bilibili.com/${d.mid}/" target="_blank" rel="noopener">${esc(d.name || r.name)}</a>`;
          if (d.live_status === 1) {
            headExtra = `<a class="live-badge" href="${esc(d.live_url || "https://live.bilibili.com/")}" target="_blank" rel="noopener">● 直播中 · ${esc(d.live_title || "")} →</a>`;
            body = "";
          } else {
            body = "";  // 未开播不显示
          }
        }
        if (d.kind === "github") {
          ghIcon = true;
          nameHTML = `<a class="up-link" href="https://github.com/${esc(d.owner || "")}/${esc(d.repo || "")}" target="_blank" rel="noopener">${esc(d.title || r.name)}</a>`;
          subHTML = `<div class="mon-sub">@${esc(d.owner || "")}${d.desc ? " · " + esc(d.desc) : ""}</div>`;
        }
        const metrics = (d.metrics || []).length
          ? `<div class="up-metrics">${d.metrics.map((m) =>
              `<div class="up-metric"><div class="v">${esc(m.value)}</div><div class="l">${esc(m.label)}${m.delta ? ` <i>${esc(m.delta)}</i>` : ""}</div></div>`).join("")}</div>`
          : `<div class="mon-value">${esc(d.title || "")}</div>`;
        body += metrics;
      } else {
        body = `<div class="mon-value">${esc(r.value)}</div>`;
      }
    } catch (e) {
      body = `<div class="mon-value">${esc(r.value)}</div>`;
    }
  } else if (r.last_error) {
    body = `<div class="mon-value dim">— 抓取失败 —</div><div class="mon-err">${esc(r.last_error)}</div>`;
  } else {
    body = `<div class="mon-value dim">尚未抓取</div>`;
  }
  const cover = img
    ? `<img class="mon-cover" src="${esc(img)}" referrerpolicy="no-referrer" onerror="this.style.display='none'">`
    : ghIcon
      ? `<div class="mon-cover gh">${GH_SVG}</div>`
      : `<div class="mon-cover ph">${esc((r.name || "?")[0])}</div>`;
  return `
  <div class="mon-card ${r.last_error ? "err" : ""}" data-id="${r.id}">
    ${cover}
    <div class="mon-main">
      <div class="mon-head"><span class="mon-name">${nameHTML}</span>${headExtra}${monBadge(cfg)}${(r.tags || []).map(mtag).join("")}</div>
      ${subHTML}
      ${body}
      <div class="mon-foot"><span>${r.fetched_at ? fmtTime(r.fetched_at) + " 更新" : "尚未抓取"} · ${r.interval_minutes || 60}分钟</span>
        <span class="spacer"></span>
        <button class="mini-btn icon" title="刷新" onclick="monRefresh('${r.id}')">${ICON_REFRESH}</button>
        <button class="mini-btn icon" title="编辑" onclick="openMonModal('${r.id}')">${ICON_EDIT}</button>
        <button class="mini-btn icon del" title="删除" onclick="monDelete('${r.id}')">${ICON_DEL}</button></div>
    </div>
  </div>`;
}

async function loadMonitors() {
  try {
    const { rules } = await api("/api/monitor/rules");
    const list = monFilter === "all"
      ? rules
      : rules.filter((r) => {
          const t = (r.config || {}).type;
          return monFilter === "stat" ? t === "bilibili_stat" : t === monFilter;
        });
    $("mon-grid").innerHTML = list.map(monCardHTML).join("") ||
      `<div class="empty">暂无监控规则，点击右上角新增</div>`;
  } catch (e) {
    $("mon-grid").innerHTML = `<div class="empty">加载失败：${esc(e.message)}</div>`;
  }
}

$("monFilterBar").addEventListener("click", (e) => {
  const b = e.target.closest(".fbtn");
  if (!b) return;
  document.querySelectorAll("#monFilterBar .fbtn").forEach((x) => x.classList.remove("active"));
  b.classList.add("active");
  monFilter = b.dataset.t;
  loadMonitors();
});

async function monRefresh(id) {
  // 立即清空数据栏与状态栏，显示"刷新中…"
  const card = document.querySelector(`.mon-card[data-id="${id}"]`);
  if (card) {
    const valueEl = card.querySelector(".mon-value");
    if (valueEl) {
      valueEl.className = "mon-value dim";
      valueEl.textContent = "刷新中…";
    }
    const foot = card.querySelector(".mon-foot");
    if (foot) {
      const span = foot.querySelector("span");
      if (span) span.textContent = "刷新中…";
    }
  }
  try {
    await api(`/api/monitor/refresh/${id}`, { method: "POST" });
  } catch (e) { toast("刷新失败", e.message); }
  loadMonitors();
}

async function monDelete(id) {
  if (!confirm("删除该监控规则？")) return;
  await api(`/api/monitor/rules/${id}`, { method: "DELETE" }).catch((e) => toast("删除失败", e.message));
  loadMonitors();
}

/* 监控模式：up / stat / xpath（老规则编辑） */
let monMode = "";
let monSel = [];

function renderMonItems() {
  const area = $("monItemArea");
  if (monMode === "up") {
    const items = [["live", "直播间开播"], ["following", "关注数"], ["follower", "粉丝数"],
                   ["view", "播放量"], ["likes", "获赞数"]];
    area.innerHTML = `<div class="field"><label>监控项（可多选，点击切换）</label>
      <div class="up-item-row">${items.map(([k, lbl]) =>
        `<button type="button" class="btn${monSel.includes(k) ? " primary" : ""}" data-item="${k}" onclick="toggleUpItem(this)">${lbl}</button>`).join("")}</div></div>`;
  } else if (monMode === "stat") {
    const items = [["view", "播放量", "▶"], ["like", "点赞", "👍"], ["coin", "投币", "🪙"],
                   ["favorite", "收藏", "⭐"], ["share", "转发", "↗"], ["danmaku", "弹幕", "💬"],
                   ["reply", "评论数", "🗨"]];
    area.innerHTML = `<div class="field"><label>监控指标（可多选，点击切换）</label>
      <div class="up-item-row">${items.map(([k, lbl, icon]) =>
        `<button type="button" class="btn${monSel.includes(k) ? " primary" : ""}" data-field="${k}" onclick="toggleStatField(this)">${icon} <span style="font-size:12px">${lbl}</span></button>`).join("")}</div></div>`;
  } else if (monMode === "github") {
    area.innerHTML = `<div class="field"><label>监控指标（可多选，点击切换）</label>
      <div class="up-item-row">${GH_FIELD_LABEL.map(([k, icon, lbl]) =>
        `<button type="button" class="btn${monSel.includes(k) ? " primary" : ""}" data-field="${k}" onclick="toggleStatField(this)">${icon} <span style="font-size:12px">${lbl}</span></button>`).join("")}</div></div>`;
  }
  area.style.display = monMode ? "" : "none";
}

function openMonModal(id) {
  monEditingTags = [];
  tagColor = PALETTE[0];
  monMode = "";
  monSel = [];
  $("monId").value = id || "";
  $("monModalTitle").textContent = id ? "编辑监控" : "新增监控";
  $("monName").value = ""; $("monUrl").value = ""; $("monXpath").value = "";
  $("monInterval").value = ""; $("monPageUrl").value = "";
  $("monInspectResult").innerHTML = ""; $("monItemArea").innerHTML = "";
  $("monType").value = ""; $("monAid").value = ""; $("monField").value = "";
  $("monXpathSection").style.display = "none";
  document.querySelectorAll('input[name="monScraper"]').forEach((r) => (r.checked = r.value === "playwright"));
  $("monTagText").value = "";
  renderColorRow(); renderMonTags();
  if (id) {
    api("/api/monitor/rules").then(({ rules }) => {
      const r = rules.find((x) => x.id === id);
      if (!r) return;
      $("monName").value = r.name;
      $("monInterval").value = r.refresh_interval_minutes || "";
      document.querySelectorAll('input[name="monScraper"]').forEach((x) => (x.checked = x.value === r.scraper));
      monEditingTags = (r.tags || []).map((t) => ({ ...t }));
      const cfg = r.config || {};
      if (cfg.type === "bilibili_stat") {
        monMode = "stat";
        monSel = cfg.fields || [cfg.field];
        $("monType").value = "bilibili_stat";
        $("monAid").value = cfg.aid || "";
        $("monField").value = monSel.join(",");
        $("monPageUrl").value = `https://www.bilibili.com/video/av${cfg.aid}`;
        $("monInspectResult").innerHTML =
          `<div style="color:var(--txt2);font-size:12px">B 站视频（aid=${esc(cfg.aid || "")}）· 选择要监控的指标</div>`;
        renderMonItems();
      } else if (cfg.type === "up") {
        monMode = "up";
        monSel = cfg.items || ["live"];
        $("monType").value = "up";
        $("monPageUrl").value = `https://space.bilibili.com/${cfg.mid}`;
        $("monInspectResult").innerHTML =
          `<div style="color:var(--txt2);font-size:12px">UP 主（mid=${esc(cfg.mid)}）· 选择要监控的项</div>`;
        renderMonItems();
      } else {
        monMode = "xpath";
        $("monType").value = "xpath";
        $("monUrl").value = cfg.url || "";
        $("monXpath").value = cfg.xpath || "";
        $("monXpathSection").style.display = "";
      }
      renderMonTags();
    });
  }
  $("monModal").classList.add("show");
}

/* 页面链接输入自动识别：UP 主页 → UP 监控；视频链接 → 视频监控 */
let monInspectTimer = null;
$("monPageUrl").addEventListener("input", () => {
  clearTimeout(monInspectTimer);
  const v = $("monPageUrl").value.trim();
  const upM = v.match(/space\.bilibili\.com\/(\d+)/);
  const ghM = v.match(/github\.com\/([\w.-]+)\/([\w.-]+)/);
  if (upM) {
    monInspectTimer = setTimeout(() => monAutoParseUp(v, upM[1]), 450);
  } else if (ghM) {
    monInspectTimer = setTimeout(() => monGithubParse(v, ghM[1], ghM[2]), 450);
  } else if (/BV[1-9A-HJ-NP-Za-km-z]{10}/.test(v)) {
    monInspectTimer = setTimeout(() => inspectPage(), 450);
  } else {
    monMode = "";
    monSel = [];
    $("monInspectResult").innerHTML = "";
    $("monItemArea").innerHTML = "";
    $("monItemArea").style.display = "none";
  }
});

/* GitHub 仓库链接识别：github.com/{owner}/{repo} → 选择监控指标 */
let monGithub = null;
const GH_SVG = `<svg viewBox="0 0 24 24" fill="#24292f" xmlns="http://www.w3.org/2000/svg"><path d="M12 .5C5.37.5 0 5.87 0 12.5c0 5.3 3.44 9.8 8.21 11.39.6.11.82-.26.82-.58 0-.29-.01-1.05-.02-2.06-3.34.73-4.04-1.61-4.04-1.61-.55-1.39-1.34-1.76-1.34-1.76-1.09-.75.08-.73.08-.73 1.21.09 1.84 1.24 1.84 1.24 1.07 1.84 2.81 1.31 3.5 1 .11-.78.42-1.31.76-1.61-2.66-.3-5.47-1.33-5.47-5.93 0-1.31.47-2.38 1.24-3.22-.12-.3-.54-1.52.12-3.17 0 0 1.01-.32 3.3 1.23a11.5 11.5 0 0 1 6.01 0c2.29-1.55 3.3-1.23 3.3-1.23.66 1.65.24 2.87.12 3.17.77.84 1.24 1.91 1.24 3.22 0 4.61-2.81 5.63-5.48 5.93.43.37.81 1.1.81 2.22 0 1.6-.01 2.9-.01 3.29 0 .32.22.7.82.58A12 12 0 0 0 24 12.5C24 5.87 18.63.5 12 .5z"/></svg>`;
const GH_FIELD_LABEL = [
  ["stars", "⭐", "星标"], ["forks", "🍴", "复刻"], ["issues", "🐛", "议题"],
  ["watchers", "👁", "关注"], ["downloads", "⬇", "下载"],
];
function monGithubParse(url, owner, repo) {
  const resultEl = $("monInspectResult");
  resultEl.innerHTML = '<span style="color:var(--txt3)">识别 GitHub 仓库中…</span>';
  $("monType").value = "github_repo";
  monGithub = { owner, repo };
  if (!$("monName").value) $("monName").value = `${owner}/${repo}`;
  monMode = "github";
  monSel = ["stars"];
  resultEl.innerHTML =
    `<div style="color:var(--txt2);font-size:12px">识别为 GitHub 仓库：<b>${esc(owner)}/${esc(repo)}</b> · 选择要监控的指标</div>`;
  renderMonItems();
}

async function monAutoParseUp(url, mid) {
  const resultEl = $("monInspectResult");
  resultEl.innerHTML = '<span style="color:var(--txt3)">识别 UP 中…</span>';
  try {
    const r = await api("/api/monitor/parse-up", { method: "POST", body: { url } });
    if (!r.name) { resultEl.innerHTML = ""; return; }
    if (!$("monName").value) $("monName").value = r.name;
    $("monType").value = "up";
    monMode = "up";
    monSel = ["live"];
    resultEl.innerHTML = `<span class="uid">已识别 UP：${esc(r.name)}（mid ${r.mid}）</span>`;
    renderMonItems();
  } catch (e) {
    resultEl.innerHTML = `<span style="color:var(--red)">识别失败：${esc(e.message)}</span>`;
  }
}

function toggleUpItem(btn) {
  btn.classList.toggle("primary");
  monSel = getSelectedItems();
}

function getSelectedItems() {
  if (monMode === "up") {
    return Array.from(document.querySelectorAll("#monItemArea .btn.primary"))
      .map((b) => b.dataset.item).filter(Boolean);
  }
  if (monMode === "stat") {
    return Array.from(document.querySelectorAll("#monItemArea .btn.primary"))
      .map((b) => b.dataset.field).filter(Boolean);
  }
  if (monMode === "github") {
    return Array.from(document.querySelectorAll("#monItemArea .btn.primary"))
      .map((b) => b.dataset.field).filter(Boolean);
  }
  return [];
}

const FIELD_LABEL = {
  view: "播放量", like: "点赞", coin: "投币", favorite: "收藏",
  share: "转发", danmaku: "弹幕", reply: "评论数",
};

async function inspectPage() {
  const url = $("monPageUrl").value.trim();
  const resultEl = $("monInspectResult");
  if (!url) { resultEl.innerHTML = '<span style="color:var(--red)">请输入页面链接</span>'; return; }
  resultEl.innerHTML = '<span style="color:var(--txt3)">识别中…</span>';
  try {
    const r = await api("/api/bilibili/inspect", { method: "POST", body: { url } });
    if (!r.ok) {
      resultEl.innerHTML = `<span style="color:var(--red)">识别失败：${esc(r.errors?.url || r.errors?.view || "未知错误")}</span>`;
      return;
    }
    $("monType").value = "bilibili_stat";
    $("monAid").value = r.aid || "";
    if (!$("monName").value) $("monName").value = r.title || "";
    monMode = "stat";
    monSel = ["view"];
    resultEl.innerHTML =
      `<div style="color:var(--txt2);font-size:12px">识别为 B 站视频：<b>${esc(r.title || "")}</b> · ${esc(r.author || "")}（aid=${esc(r.aid || "")}）· 选择要监控的指标</div>`;
    renderMonItems();
  } catch (e) { resultEl.innerHTML = `<span style="color:var(--red)">识别失败：${esc(e.message)}</span>`; }
}

function toggleStatField(btn) {
  btn.classList.toggle("primary");
  monSel = getSelectedItems();
}

function renderColorRow() {
  $("monColorRow").innerHTML = PALETTE.map((c) =>
    `<button class="cchip ${c === tagColor ? "sel" : ""}" style="background:${c}" onclick="pickColor(this)"></button>`).join("");
}
function pickColor(el) {
  tagColor = el.dataset.c || el.style.background;
  document.querySelectorAll("#monColorRow .cchip").forEach((x) => x.classList.remove("sel"));
  el.classList.add("sel");
}
function addTag() {
  const t = $("monTagText").value.trim();
  if (!t) return;
  monEditingTags.push({ t: t.slice(0, 20), c: tagColor });
  $("monTagText").value = "";
  renderMonTags();
}
function renderMonTags() {
  $("monTags").innerHTML = monEditingTags.map((t, i) =>
    `<span class="mtag" style="background:${t.c}1f;color:${t.c};border-color:${t.c}55">${esc(t.t)} <span class="x" onclick="removeTag(${i})">×</span></span>`).join("");
}
function removeTag(i) { monEditingTags.splice(i, 1); renderMonTags(); }

async function saveMonitor() {
  const id = $("monId").value;
  const rtype = $("monType").value;
  let config;
  if (rtype === "bilibili_stat") {
    const aid = parseInt($("monAid").value, 10);
    const fields = getSelectedItems();
    if (!aid || fields.length === 0) { toast("提示", "请先识别视频链接并至少选一个指标"); return; }
    config = { type: "bilibili_stat", aid, fields };
  } else if (rtype === "up") {
    const m = $("monPageUrl").value.trim().match(/space\.bilibili\.com\/(\d+)/);
    if (!m) { toast("提示", "请先输入 UP 主页链接"); return; }
    const mid = parseInt(m[1], 10);
    const items = getSelectedItems();
    if (items.length === 0) { toast("提示", "至少选一个监控项"); return; }
    config = { type: "up", mid, items };
  } else if (rtype === "github_repo") {
    const m = $("monPageUrl").value.trim().match(/github\.com\/([\w.-]+)\/([\w.-]+)/);
    if (!m) { toast("提示", "请先输入 GitHub 仓库链接"); return; }
    const fields = getSelectedItems();
    if (fields.length === 0) { toast("提示", "至少选一个监控指标"); return; }
    config = { type: "github_repo", owner: m[1], repo: m[2], fields };
  } else {
    config = { url: $("monUrl").value.trim(), xpath: $("monXpath").value.trim() };
  }
  const body = {
    name: $("monName").value.trim(),
    scraper: document.querySelector('input[name="monScraper"]:checked')?.value || "requests",
    refresh_interval_minutes: parseInt($("monInterval").value, 10) || 0,
    tags: monEditingTags,
    config,
  };
  try {
    const res = id
      ? await api(`/api/monitor/rules/${id}`, { method: "PUT", body })
      : await api("/api/monitor/rules", { method: "POST", body });
    // 保存后立即刷新一次该监控
    let msg = "监控规则已更新";
    try {
      const fr = await api(`/api/monitor/refresh/${res.rule.id}`, { method: "POST" });
      if (fr.triggered) msg = `已刷新，${fr.triggered} 项数据更新`;
      else if (fr.error) msg = `已保存，但刷新失败：${fr.error}`;
    } catch (e) { msg = `已保存，刷新失败：${e.message}`; }
    closeModal("monModal");
    loadMonitors();
    toast("已保存", msg);
  } catch (e) { toast("保存失败", e.message); }
}

/* ============ 下载 ============ */
let dlList = [];
let dlSel = new Set();

async function loadDownloads() {
  try {
    const { downloads } = await api("/api/downloads");
    dlList = downloads || [];
    $("dl-list").innerHTML = dlList.map((d) => {
      const stMap = { pending: "排队中", running: "下载中", success: "已完成", failed: "失败", canceled: "已取消" };
      const cls = d.status === "success" ? "ok" : d.status === "failed" ? "bad" : "";
      const typeMap = { video: "视频", audio: "音频", danmaku: "弹幕" };
      const typeColor = { video: ["#eaf1fe", "#2f6fed", "#bfd7ff"], audio: ["#e8f7ee", "#16a34a", "#bce7cd"], danmaku: ["#f3effe", "#7c3aed", "#d8cdfb"] };
      const raw = (d.content_type || "video") === "both" ? "video+danmaku" : (d.content_type || "video");
      const typeTags = raw.split("+").map((t) => {
        const [bg, fg, bd] = typeColor[t] || ["#eef0f3", "#6b7280", "#d5dae1"];
        return `<span class="mtag" style="background:${bg};color:${fg};border-color:${bd}">${typeMap[t] || t}</span>`;
      }).join("");
      return `<div class="dl-row" title="双击打开文件夹" ondblclick="openDir(${d.id})">
        <input type="checkbox" ${dlSel.has(d.id) ? "checked" : ""} onchange="toggleDl(${d.id}, this.checked)">
        <div class="dl-title">${esc(d.title)}</div>
        ${typeTags}
        <span class="st ${esc(d.status)}">${stMap[d.status] || d.status}</span>
        <div class="progress"><div class="bar ${cls}" style="width:${d.progress || 0}%"></div></div>
        <span class="pct">${Math.round(d.progress || 0)}%</span>
        <div class="dl-dir" title="${esc(d.error || "")}">${esc(d.save_dir)}/${esc(sanitizeName(d.title))}</div>
        ${d.status === "running" || d.status === "pending"
          ? `<button class="link-btn" onclick="dlOne(${d.id},'stop')">停止</button>` : ""}
      </div>`;
    }).join("") || `<div class="empty">暂无下载任务</div>`;
  } catch (e) {
    $("dl-list").innerHTML = `<div class="empty">加载失败：${esc(e.message)}</div>`;
  }
}

function toggleDl(id, on) {
  if (on) dlSel.add(id); else dlSel.delete(id);
  $("selAll").checked = dlList.length > 0 && dlList.every((d) => dlSel.has(d.id));
}
$("selAll").addEventListener("change", (e) => {
  dlSel = e.target.checked ? new Set(dlList.map((d) => d.id)) : new Set();
  loadDownloads();
});

async function dlOne(id, action) {
  if (action === "stop" || action === "pause") {
    await api(`/api/downloads/${id}/cancel`, { method: "POST" }).catch((e) => toast("操作失败", e.message));
  } else if (action === "start") {
    await api(`/api/downloads/${id}/resume`, { method: "POST" }).catch((e) => toast("操作失败", e.message));
  } else if (action === "delete") {
    await api(`/api/downloads/${id}`, { method: "DELETE" }).catch((e) => toast("删除失败", e.message));
    dlSel.delete(id);
  }
  loadDownloads();
}

async function dlBatch(action) {
  if (action === "delete" && !confirm("删除选中的下载任务？")) return;
  if (dlSel.size === 0) { toast("提示", "未选择任何任务"); return; }
  for (const id of [...dlSel]) await dlOne(id, action);
  if (action === "delete") dlSel.clear();
  toast("完成", `已对 ${dlSel.size || 1} 个任务执行操作`);
}

async function openDir(id) {
  try {
    const res = await api("/api/downloads/open-dir", { method: "POST", body: { ids: [id] } });
    toast("已打开文件夹", res.path || res.note || "");
  } catch (e) { toast("打开失败", e.message); }
}

async function dlOpenDir() {
  if (dlSel.size === 0) { toast("提示", "未选择任何任务"); return; }
  const first = dlList.find((d) => dlSel.has(d.id));
  if (!first) return;
  await openDir(first.id);
}

/* 下载面板 */
let dlTargetItem = null;
function openDlModal(itemId) {
  api(`/api/items?status=all`).then(({ items }) => {
    const it = (items || []).find((x) => x.id === itemId);
    if (!it) return;
    dlTargetItem = it;
    $("dlTitle").textContent = it.title;
    $("dlCov").innerHTML = it.cover
      ? `<img src="${esc(it.cover)}" referrerpolicy="no-referrer" onerror="this.style.display='none'">` : "";
    api("/api/config").then((cfg) => { $("dlDir").value = cfg.download_dir || "data/downloads"; });
    $("dlVideo").checked = true; $("dlAudio").checked = false; $("dlDanmaku").checked = false;
    $("dlQuality").value = "best";
    $("dlModal").classList.add("show");
  });
}

async function startDownload() {
  if (!dlTargetItem) return;
  const content = [];
  if ($("dlVideo").checked) content.push("video");
  if ($("dlAudio").checked) content.push("audio");
  if ($("dlDanmaku").checked) content.push("danmaku");
  if (content.length === 0) { toast("提示", "请至少选择一种下载内容"); return; }
  const content_type = content.join("+");
  try {
    await api(`/api/items/${dlTargetItem.id}/download`, {
      method: "POST",
      body: { content_type, quality: $("dlQuality").value, save_dir: $("dlDir").value.trim() },
    });
    closeModal("dlModal");
    toast("已加入下载队列", "将在后台串行执行");
  } catch (e) { toast("创建失败", e.message); }
}

/* ============ 订阅 ============ */
let parsedUid = null;
let defaultInterval = 60;

async function loadSubs() {
  try {
    const [subsRes, cfg] = await Promise.all([api("/api/subscriptions"), api("/api/config")]);
    defaultInterval = cfg.default_refresh_minutes || 60;
    $("sub-tbody").innerHTML = (subsRes.subscriptions || []).map((s) => {
      const isUgc = s.adapter === "ugc";
      const idShow = isUgc ? s.config.bvid : (s.config.up_name || s.config.uid);
      const interval = s.refresh_interval_minutes || defaultInterval;
      return `
      <tr>
        <td class="sub-name">${esc(s.name)}</td>
        <td>${isUgc
          ? `<span class="mtag" style="background:#fdf0f4;color:#d4537e;border-color:#f4c0d1">合集</span>`
          : `<span class="mtag" style="background:#eaf1fe;color:#2f6fed;border-color:#bfd7ff">${esc(s.adapter)}</span>`}</td>
        <td class="uid">${esc(idShow)}</td>
        <td><div class="kws">${s.config.keywords.map((k) => `<span class="tag">${esc(k.text)}</span>`).join("")}${(s.config.exclude_keywords || []).map((k) => `<span class="tag excl">${esc(k.text)}</span>`).join("")}</div></td>
        <td style="color:var(--txt2)">${interval} 分钟</td>
        <td><button class="switch ${s.enabled ? "on" : ""}" onclick="toggleSub('${s.id}', this)"></button></td>
        <td><div class="row-actions">
          <button class="link-btn" onclick="subRefresh('${s.id}')">刷新</button>
          <button class="link-btn" onclick="openSubModal('${s.id}')">编辑</button>
          <button class="link-btn" style="color:var(--red)" onclick="subDelete('${s.id}')">删除</button></div></td>
      </tr>`;
    }).join("") || `<tr><td colspan="7" class="empty">暂无订阅，点击右上角新增</td></tr>`;
  } catch (e) {
    $("sub-tbody").innerHTML = `<tr><td colspan="7" class="empty">加载失败：${esc(e.message)}</td></tr>`;
  }
}

async function subRefresh(id) {
  let name = "该订阅";
  try {
    const { subscriptions } = await api("/api/subscriptions");
    const s = subscriptions.find((x) => x.id === id);
    if (s) name = s.name;
  } catch (e) { /* 忽略，用默认名 */ }
  toast("正在刷新", `订阅「${name}」正在后台刷新`);
  await runRefresh(() => api(`/api/refresh/${id}`, { method: "POST" }),
                   `订阅「${name}」已同步`);
}

/* ============ 视频合集导入（订阅弹窗内联） ============ */
let ugcInfo = null;

function hideUgcInline() {
  ugcInfo = null;
  $("ugcInline").style.display = "none";
  $("ugcInline").innerHTML = "";
}

async function ugcInlineParse() {
  const url = $("subUidInput").value.trim();
  if (!/BV[1-9A-HJ-NP-Za-km-z]{10}/.test(url)) return;
  parsedUid = null;
  $("subUidShow").textContent = "";
  $("ugcInline").style.display = "block";
  $("ugcInline").innerHTML = `<div style="color:var(--txt3);font-size:13px">检测到视频链接，解析中…</div>`;
  try {
    const { collection } = await api("/api/ugc/parse", { method: "POST", body: { url } });
    ugcInfo = collection;
    // 视频的 UP 即可作为订阅 UID：粘贴视频链接也能正常保存订阅
    parsedUid = collection.up_uid;
    $("subUidShow").textContent = `UP: ${collection.up_name} · UID: ${collection.up_uid}`;
    $("ugcInline").innerHTML = `
      <div style="display:flex;gap:10px;align-items:center;border:1px solid var(--border);border-radius:8px;padding:8px">
        <img src="${esc(collection.pic)}" referrerpolicy="no-referrer"
             style="width:72px;height:48px;object-fit:cover;border-radius:6px">
        <div style="flex:1;min-width:0">
          <div style="font-weight:500;font-size:13px">${esc(collection.title)}</div>
          <div style="color:var(--txt3);font-size:12px">${esc(collection.up_name)} · ${collection.pages.length} 个分P</div>
          <div style="color:var(--blue);font-size:12px;margin-top:2px">识别为视频合集，点"保存"将订阅并导入全部 ${collection.pages.length} 集到待办</div>
        </div>
      </div>`;
  } catch (e) {
    $("ugcInline").innerHTML = `<div style="color:var(--red);font-size:13px">解析失败：${esc(e.message)}</div>`;
  }
}

async function toggleSub(id, el) {
  const subs = (await api("/api/subscriptions")).subscriptions;
  const s = subs.find((x) => x.id === id);
  if (!s) return;
  s.enabled = !s.enabled;
  await api(`/api/subscriptions/${id}`, { method: "PUT", body: s }).catch((e) => toast("操作失败", e.message));
  el.classList.toggle("on", s.enabled);
}

async function subDelete(id) {
  if (!confirm("删除该订阅？将同时删除该订阅下的所有待办条目。")) return;
  await api(`/api/subscriptions/${id}`, { method: "DELETE" }).catch((e) => toast("删除失败", e.message));
  loadSubs();
}

let subEdit = { match_logic: "all", fetch_mode: "latest" };

function openSubModal(id) {
  parsedUid = null;
  $("subId").value = id || "";
  $("subModalTitle").textContent = id ? "编辑订阅" : "新增订阅";
  $("subName").value = ""; $("subUidInput").value = ""; $("subKeywords").value = "";
  $("subInterval").value = ""; $("subUidShow").textContent = "";
  hideUgcInline();
  $("subDepth").value = ""; $("subExclude").value = "";
  $("subEnabled").classList.add("on");
  subEdit = { match_logic: "all", fetch_mode: "latest" };
  if (id) {
    api("/api/subscriptions").then(({ subscriptions }) => {
      const s = subscriptions.find((x) => x.id === id);
      if (!s) return;
      subEdit = { match_logic: s.config.match_logic || "all", fetch_mode: s.config.fetch_mode || "latest" };
      $("subName").value = s.name;
      if (s.adapter === "ugc") {
        $("subUidInput").value = `https://www.bilibili.com/video/${s.config.bvid}`;
        $("subUidShow").textContent = `合集订阅 · ${s.config.bvid}`;
        parsedUid = null;
      } else {
        $("subUidInput").value = s.config.uid;
        $("subUidShow").textContent = `UID: ${s.config.uid}`;
        parsedUid = s.config.uid;
      }
      $("subKeywords").value = s.config.keywords.map((k) => k.text).join(", ");
      $("subExclude").value = (s.config.exclude_keywords || []).map((k) => k.text).join(", ");
      $("subInterval").value = s.refresh_interval_minutes || "";
      $("subDepth").value = s.config.fetch_depth || "";
      $("subEnabled").classList.toggle("on", s.enabled);
    });
  }
  $("subModal").classList.add("show");
}

let uidTimer = null;
$("subUidInput").addEventListener("input", () => {
  clearTimeout(uidTimer);
  const v = $("subUidInput").value.trim();
  if (/BV[1-9A-HJ-NP-Za-km-z]{10}/.test(v)) {
    uidTimer = setTimeout(ugcInlineParse, 450);  // 视频/合集链接
  } else {
    hideUgcInline();
    uidTimer = setTimeout(parseUid, 450);        // UP 链接/UID
  }
});

async function parseUid() {
  const input = $("subUidInput").value.trim();
  if (!input) {
    parsedUid = null;
    $("subUidShow").textContent = "";
    return;
  }
  try {
    const { uid } = await api("/api/subscriptions/parse-uid", { method: "POST", body: { url: input } });
    parsedUid = uid;
    $("subUidShow").textContent = `解析成功，UID: ${uid}`;
  } catch (e) {
    parsedUid = null;
    $("subUidShow").textContent = "";
  }
}

async function saveSubscription() {
  const id = $("subId").value;
  const uidInput = $("subUidInput").value.trim();
  const keywords = $("subKeywords").value.split(/[,，]/).map((s) => s.trim()).filter(Boolean)
    .map((t) => ({ text: t, regex: false, case_sensitive: false }));
  const exclude_keywords = $("subExclude").value.split(/[,，]/).map((s) => s.trim()).filter(Boolean)
    .map((t) => ({ text: t, regex: false, case_sensitive: false }));
  const bv = (uidInput.match(/BV[1-9A-HJ-NP-Za-km-z]{10}/) || [null])[0]
          || (ugcInfo ? ugcInfo.bvid : null);
  let body;
  if (bv) {
    // 合集/视频订阅：保存 bvid，刷新时按 view 接口监控分P 更新
    body = {
      name: $("subName").value.trim() || (ugcInfo ? ugcInfo.title : bv),
      adapter: "ugc",
      enabled: $("subEnabled").classList.contains("on"),
      refresh_interval_minutes: parseInt($("subInterval").value, 10) || 0,
      config: { bvid: bv, keywords, exclude_keywords, match_logic: "all", fetch_mode: "latest", fetch_depth: 1 },
    };
  } else {
    let uid = parsedUid;
    if (!uid && /^\d{6,12}$/.test(uidInput)) uid = parseInt(uidInput, 10);
    if (!uid) { toast("提示", "请先解析 UID 或输入有效 UID"); return; }
    // 关键词为空：按 fetch_depth 自动跟踪最新 N 条（保存时立即抓取一次）
    body = {
      name: $("subName").value.trim(),
      enabled: $("subEnabled").classList.contains("on"),
      refresh_interval_minutes: parseInt($("subInterval").value, 10) || 0,
      config: {
        uid,
        keywords,
        exclude_keywords,
        match_logic: subEdit.match_logic,
        fetch_mode: subEdit.fetch_mode,
        fetch_depth: parseInt($("subDepth").value, 10) || 30,
      },
    };
  }
  try {
    const res = id
      ? await api(`/api/subscriptions/${id}`, { method: "PUT", body })
      : await api("/api/subscriptions", { method: "POST", body });
    if (bv && res.subscription) {
      // 保存合集订阅后，自动把全部分P 导入待办（挂到该订阅 id，与刷新共用去重键）
      const imp = await api("/api/ugc/import", { method: "POST", body: { url: uidInput, sub_id: res.subscription.id } })
        .catch((e) => ({ imported: 0, skipped: 0, err: e.message }));
      if (imp.err) toast("已保存", `订阅已保存，但导入分P 失败：${imp.err}`);
      else toast("已保存", `合集订阅已保存，导入 ${imp.imported} 集 · 跳过 ${imp.skipped}（已存在）`);
    } else {
      let msg = "订阅已更新";
      // 新建且关键词为空：立即按深度抓取最新视频（去重自动处理）
      if (!id && keywords.length === 0 && res.subscription) {
        try {
          const fr = await api(`/api/refresh/${res.subscription.id}?sync=1`, { method: "POST" });
          const n = fr.new ?? 0;
          msg = n > 0
            ? `已保存，自动抓取最新 ${n} 条视频入待办`
            : "已保存，没有新的视频（均已存在）";
        } catch (e) { msg = `已保存，抓取失败：${e.message}`; }
      }
      toast("已保存", msg);
    }
    closeModal("subModal");
    loadSubs();
    await Promise.all([loadItems(), loadStats()]);
  } catch (e) { toast("保存失败", e.message); }
}

/* ============ 设置 ============ */
async function loadSettings() {
  try {
    const cfg = await api("/api/config");
    $("cfg-port").value = cfg.port || 8848;
    $("cfg-interval").value = cfg.default_refresh_minutes || 60;
    $("cfg-dldir").value = cfg.download_dir || "data/downloads";
    document.querySelectorAll('input[name="cfg-scraper"]').forEach((r) =>
      (r.checked = r.value === (cfg.default_scraper || "requests")));
    const st = await api("/api/startup");
    $("cfg-startup").classList.toggle("on", !!st.enabled);
    loadAccount();
    // 关于：填充版本号 / 作者 / GitHub 链接
    try {
      const info = await api("/api/app-info");
      $("aboutName").textContent = info.name || "RSS_Todo";
      $("aboutMeta").textContent = `v${info.version || "?"} · 作者 ${info.author || ""}`;
      if (info.repo_url) $("aboutGh").href = info.repo_url;
    } catch (e) { /* 关于信息加载失败不影响设置页 */ }
  } catch (e) { toast("加载设置失败", e.message); }
}

/* ============ B 站扫码登录 ============ */
let qrPollTimer = null;
let qrKey = null;

async function loadAccount() {
  const el = $("biliAccount");
  if (!el) return;
  try {
    const info = await api("/api/bilibili/login/account");
    if (info.isLogin) {
      el.innerHTML = `
        <div class="acc-card">
          <img class="acc-avatar" src="${esc(info.face || "")}" referrerpolicy="no-referrer" onerror="this.style.display='none'">
          <div class="acc-info">
            <div style="display:flex;align-items:center;gap:8px">
              <span class="acc-name">${esc(info.uname || "未命名")}</span>
              <span class="st success" style="padding:1px 8px">已登录</span>
            </div>
            <div class="acc-meta">UID: ${esc(info.mid ?? "—")}${info.level ? ` · Lv${info.level}` : ""}</div>
          </div>
          <button class="btn ghost danger" onclick="logoutBili()">退出登录</button>
        </div>`;
    } else {
      el.innerHTML = `
        <div style="display:flex;align-items:center;gap:10px">
          <span style="color:var(--txt3)">未登录 B 站（搜索/高清晰度下载需登录）</span>
          <button class="btn primary" onclick="openQrLogin()">扫码登录 B 站</button>
        </div>`;
    }
  } catch (e) {
    el.innerHTML = `<span style="color:var(--red)">加载账号状态失败</span>`;
  }
}

async function openQrLogin() {
  try {
    const { qrcode_key, url } = await api("/api/bilibili/login/qrcode", { method: "POST" });
    qrKey = qrcode_key;
    $("qrBox").innerHTML = "";
    $("qrStatus").textContent = "等待扫码…";
    $("qrRegen").style.display = "none";
    if (typeof QRCode !== "undefined") {
      new QRCode($("qrBox"), { text: url, width: 180, height: 180, correctLevel: QRCode.CorrectLevel.M });
    } else {
      $("qrStatus").textContent = "二维码组件加载失败";
      return;
    }
    $("qrModal").classList.add("show");
    startQrPoll();
  } catch (e) { toast("生成二维码失败", e.message); }
}

function startQrPoll() {
  clearInterval(qrPollTimer);
  let tries = 0;
  qrPollTimer = setInterval(async () => {
    tries++;
    try {
      const res = await api(`/api/bilibili/login/poll?key=${encodeURIComponent(qrKey)}`);
      if (res.status === "pending") { $("qrStatus").textContent = "等待扫码…"; }
      else if (res.status === "scanned") { $("qrStatus").textContent = res.message || "已扫码，请在手机上确认"; }
      else if (res.status === "expired") {
        $("qrStatus").textContent = "二维码已失效，请重新生成";
        $("qrRegen").style.display = "";
        clearInterval(qrPollTimer);
      } else if (res.status === "success") {
        clearInterval(qrPollTimer);
        closeModal("qrModal");
        toast("登录成功", "B 站账号已绑定");
        loadAccount();
      }
    } catch (e) { /* 网络抖动忽略 */ }
    if (tries > 90) { clearInterval(qrPollTimer); $("qrStatus").textContent = "等待超时，请重新生成"; $("qrRegen").style.display = ""; }
  }, 2000);
}

async function logoutBili() {
  if (!confirm("退出 B 站登录？搜索与高清晰度下载将受限。")) return;
  await api("/api/bilibili/login/logout", { method: "POST" }).catch(() => {});
  toast("已退出登录");
  loadAccount();
}

async function saveSettings() {
  try {
    const res = await api("/api/config", {
      method: "PUT",
      body: {
        port: parseInt($("cfg-port").value, 10) || 8848,
        default_refresh_minutes: parseInt($("cfg-interval").value, 10) || 60,
        default_scraper: document.querySelector('input[name="cfg-scraper"]:checked').value,
        download_dir: $("cfg-dldir").value.trim(),
      },
    });
    toast("已保存", res.port_changed ? "端口修改需重启 rss-todo 生效" : "配置已保存");
  } catch (e) { toast("保存失败", e.message); }
}

/* 打开下载目录 */
$("cfg-open-dldir").addEventListener("click", async () => {
  try {
    const res = await api("/api/open-download-dir", { method: "POST" });
    if (res.ok) toast("已打开", res.path);
  } catch (e) { toast("打开失败", e.message); }
});

async function toggleStartup() {
  const el = $("cfg-startup");
  const enabled = !el.classList.contains("on");
  try {
    await api("/api/startup", { method: "PUT", body: { enabled } });
    el.classList.toggle("on", enabled);
    toast(enabled ? "已开启开机启动" : "已关闭开机启动");
  } catch (e) { toast("操作失败", e.message); }
}

/* ============ 通知 / 轮询 ============ */
function notify(title, body) {
  if ("Notification" in window && Notification.permission === "granted") {
    try { new Notification(title, { body }); } catch (e) { /* 忽略 */ }
  }
}

/* 公共刷新流程：右上角按钮显示"刷新中…"转圈，轮询真实进度，完成恢复 */
async function runRefresh(trigger, doneMsg) {
  const btn = $("refreshBtn");
  const label = $("refreshLabel");
  if (btn.disabled) return;
  btn.disabled = true;
  btn.classList.add("loading");
  label.textContent = "刷新中…";
  const t0 = Date.now();
  try {
    await trigger();
    // 轮询后台刷新状态，真正完成后才恢复
    for (let i = 0; i < 60; i++) {
      await new Promise((r) => setTimeout(r, 1500));
      let st = null;
      try { st = await api("/api/refresh/status"); } catch (e) { break; }
      if (!st.running && Date.now() - t0 > 4000) break;
    }
    label.textContent = "刷新完成";
    toast("刷新完成", doneMsg || "订阅与监控已同步");
    await Promise.all([loadItems(), loadStats(), loadMonitors(), loadDownloads()]);
  } catch (e) {
    label.textContent = "刷新失败";
    toast("刷新失败", e.message);
  } finally {
    setTimeout(() => {
      btn.disabled = false;
      btn.classList.remove("loading");
      label.textContent = "刷新";
    }, 1200);
  }
}

$("refreshBtn").addEventListener("click", () => {
  toast("正在刷新", "订阅与监控正在后台同步");
  runRefresh(async () => {
    await api("/api/refresh", { method: "POST" });           // 订阅
    await api("/api/monitor/refresh", { method: "POST" });   // 监控
  });
});

/* 窗口控制：最小化 / 退出（退出同时关后端进程与浏览器窗口） */
$("minBtn").addEventListener("click", () => {
  api("/api/window/minimize", { method: "POST" }).catch((e) => toast("操作失败", e.message));
});

$("exitBtn").addEventListener("click", () => {
  if (!confirm("退出程序？将同时关闭后台服务。")) return;
  api("/api/shutdown", { method: "POST" }).catch(() => {});
  setTimeout(() => { toast("正在退出…", ""); }, 300);
});

function closeModal(id) { $(id).classList.remove("show"); }
document.querySelectorAll(".modal-mask").forEach((m) => {
  // 所有弹窗统一：单击阴影不关闭（防误触丢输入），双击阴影退出
  m.addEventListener("click", (e) => {
    if (e.target === m && e.detail >= 2) m.classList.remove("show");
  });
});

/* ============ 启动 ============ */
if ("Notification" in window && Notification.permission === "default") {
  Notification.requestPermission();
}
loadSubTabs();
loadItems();
loadStats();
setInterval(() => { loadStats(); }, 30000);
setInterval(() => { loadDownloads(); }, 3000);
