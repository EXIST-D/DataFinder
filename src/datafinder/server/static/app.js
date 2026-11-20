"use strict";
/* DataFinder 前端：无框架、无构建。
   数据流：提交 → /api/portals（毫秒级）+ /api/search（SSE，每个来源完成推一份"当前最佳快照"）→ 渲染。 */

const $ = (id) => document.getElementById(id);
const el = {
  form: $("searchForm"), q: $("q"), go: $("go"), examples: $("examples"),
  results: $("results"), sources: $("sources"), datasets: $("datasets"), count: $("count"),
  portals: $("portals"), footnote: $("footnote"), navStatus: $("navStatus"),
  scrim: $("scrim"), sheet: $("sheet"), sheetBody: $("sheetBody"), sheetClose: $("sheetClose"),
  toast: $("toast"),
};

const ICON_OUT = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M7 17L17 7M8 7h9v9"/></svg>';

const STATUS_TEXT = {
  pending: "查询中", ok: "已返回", cached: "来自缓存", stale: "用了过期缓存", skipped: "不适用",
  resting: "休息中", error: "出错", timeout: "超时", not_waited: "没等它",
};

let es = null;            // 当前 EventSource
let seq = 0;              // 查询序号，丢弃过期响应
let lastSnap = null;      // 最近一份快照
let shown = new Set();    // 已显示过的数据集链接（用于只给新条目做入场动画）
let currentRows = [];     // 当前列表对应的数据集，供详情面板取用
let lastFocus = null;
let inspectOk = false;   // 本机是否装了 Dataseek（决定能否按需读取文件列表）

/* ───────── 小工具 ───────── */
function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function safeUrl(u) {
  try { const x = new URL(u); return x.protocol === "http:" || x.protocol === "https:" ? x.href : "#"; } catch { return "#"; }
}
function fmtSize(n) {
  if (!n) return "";
  const u = ["B", "KB", "MB", "GB", "TB"]; let i = 0;
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return (i === 0 ? n : n.toFixed(1)) + " " + u[i];
}
function toast(msg) {
  el.toast.textContent = msg; el.toast.classList.add("on");
  clearTimeout(toast.t); toast.t = setTimeout(() => el.toast.classList.remove("on"), 1800);
}
async function copy(text) {
  try { await navigator.clipboard.writeText(text); }
  catch {
    const t = document.createElement("textarea"); t.value = text; t.style.position = "fixed"; t.style.opacity = "0";
    document.body.appendChild(t); t.select(); document.execCommand("copy"); t.remove();
  }
  toast("已复制");
}

/* ───────── 顶栏状态 ───────── */
async function loadStatus() {
  try {
    const s = await (await fetch("/api/status")).json();
    inspectOk = !!s.dataseek;
    el.navStatus.textContent = s.portals ? `门户库 ${s.portals.toLocaleString("zh-CN")} 条` : "门户库为空 · 请先运行 datafinder sync";
  } catch { /* 忽略：状态只是装饰 */ }
}

/* ───────── 渲染：来源状态条 ───────── */
function renderSources(list) {
  el.sources.innerHTML = list.map((s) => {
    const tip = [STATUS_TEXT[s.status] || s.status, s.message].filter(Boolean).join("：");
    const n = ["ok", "cached", "stale"].includes(s.status) ? ` <b>${s.count}</b>` : "";
    const exp = s.experimental ? " <i>实验性</i>" : "";
    return `<span class="src" data-s="${esc(s.status)}" title="${esc(tip)}"><span class="dot"></span>${esc(s.title)}${n}${exp}</span>`;
  }).join("");
}

/* ───────── 渲染：数据集 ───────── */
function rowHTML(d, i) {
  const isNew = !shown.has(d.url);
  const where = (d.where || [d.source]).slice(0, 3).join("、");
  const exp = lastSnap?.sources.find((s) => s.id === d.source)?.experimental;
  const facts = [
    `<span class="src-name">${esc(where)}${exp ? '<span class="tag-exp">实验性</span>' : ""}</span>`,
    d.license ? `<span>${esc(d.license)}</span>` : `<span class="warn">许可未知</span>`,
    d.publisher && !where.includes(d.publisher) ? `<span>${esc(d.publisher.slice(0, 40))}</span>` : "",
    d.period ? `<span>数据期 ${esc(d.period)}</span>` : "",
    d.updated ? `<span>更新 ${esc(d.updated)}</span>` : "",
  ].filter(Boolean).join("");
  const pills = (d.formats || []).slice(0, 5).map((f) => `<span class="pill">${esc(f)}</span>`).join("");
  const nres = (d.resources || []).length;
  const resPill = nres ? `<span class="pill res-pill">${nres} 个资源</span>` : "";
  return `<li class="row${isNew ? " enter" : ""}" data-i="${i}" tabindex="0" role="button" aria-label="查看详情：${esc(d.title)}">
    <div class="row-title">${esc(d.title)}</div>
    <div class="facts">${facts}</div>
    ${d.description ? `<p class="desc">${esc(d.description)}</p>` : ""}
    <div class="row-foot">${pills}${resPill}<span class="spacer"></span>
      <a class="link-btn" href="${esc(safeUrl(d.url))}" target="_blank" rel="noopener noreferrer">打开原站 ${ICON_OUT}</a>
    </div>
  </li>`;
}

function skeleton() {
  return `<div class="panel">${'<div class="skel"><i></i><i></i><i></i></div><div class="sep"></div>'.repeat(3)}</div>`;
}

function renderDatasets(snap) {
  const ds = snap.datasets;
  currentRows = ds;
  el.count.textContent = ds.length ? `${ds.length} 条${snap.candidates > ds.length ? `（共 ${snap.candidates} 条候选）` : ""}` : "";
  if (!ds.length) {
    el.datasets.innerHTML = snap.final
      ? `<div class="panel"><div class="empty"><h3>没有找到匹配的数据集</h3><p>可以去掉格式词、换个说法，或先看右侧的相关门户。</p></div></div>`
      : skeleton();
    return;
  }
  el.datasets.innerHTML = `<ol class="panel">${ds.map(rowHTML).join("")}</ol>`;
  ds.forEach((d) => shown.add(d.url));
}

/* ───────── 渲染：门户 ───────── */
function renderPortals(data) {
  if (!data.synced) {
    el.portals.innerHTML = `<div class="panel"><div class="empty"><p>门户库还是空的。<br>请在终端运行 <code>datafinder sync</code> 后刷新。</p></div></div>`;
    return;
  }
  if (!data.portals.length) {
    el.portals.innerHTML = `<div class="panel"><div class="empty"><p>没有匹配的门户</p></div></div>`;
    return;
  }
  el.portals.innerHTML = `<div class="panel">${data.portals.map((p) => {
    const badges = [p.featured ? '<span class="badge">精选</span>' : "", p.national ? '<span class="badge">国家级</span>' : "", p.api ? '<span class="badge">API</span>' : ""].join("");
    const place = [p.country, p.region].filter(Boolean).join("·");
    const sub = [place, p.type, p.industries.slice(0, 2).join(" ")].filter(Boolean).join(" · ");
    return `<a class="portal" href="${esc(safeUrl(p.url))}" target="_blank" rel="noopener noreferrer">
      <div class="portal-name">${esc(p.name)}<span class="portal-badges">${badges}</span></div>
      <div class="portal-sub">${esc(sub)}</div></a>`;
  }).join('<div class="sep"></div>')}</div>`;
}

function renderFootnote(snap) {
  const bits = [];
  if (snap.final && !snap.complete) bits.push("部分来源没有返回，结果可能不完整。");
  if (snap.datasets.some((d) => !d.license)) bits.push("“许可未知”不代表可以随意使用，请以原站声明为准。");
  el.footnote.textContent = bits.join(" ");
}

function onSnapshot(snap) {
  lastSnap = snap;
  renderSources(snap.sources);
  renderDatasets(snap);
  renderFootnote(snap);
  if (snap.final) document.body.classList.remove("loading");
}

/* ───────── 检索 ───────── */
function run(query) {
  query = query.trim();
  if (!query) return;
  const mine = ++seq;
  if (es) { es.close(); es = null; }
  closeSheet();
  shown = new Set(); lastSnap = null; currentRows = [];
  el.q.value = query; el.go.disabled = false;
  document.body.classList.add("has-results", "loading");
  el.results.hidden = false;
  el.sources.innerHTML = ""; el.count.textContent = ""; el.footnote.textContent = "";
  el.datasets.innerHTML = skeleton();
  el.portals.innerHTML = skeleton();
  history.replaceState(null, "", "?q=" + encodeURIComponent(query));
  document.title = `${query} · DataFinder`;

  // 门户：本地毫秒级，先出
  fetch("/api/portals?limit=8&q=" + encodeURIComponent(query))
    .then((r) => r.json()).then((d) => { if (mine === seq) renderPortals(d); })
    .catch(() => { if (mine === seq) el.portals.innerHTML = `<div class="panel"><div class="empty"><p>门户加载失败</p></div></div>`; });

  // 数据集：SSE 逐批
  es = new EventSource("/api/search?limit=15&q=" + encodeURIComponent(query));
  es.onmessage = (ev) => {
    if (mine !== seq) return;
    const msg = JSON.parse(ev.data);
    if (msg.type === "error") { fail(msg.message); return; }
    onSnapshot(msg);
    if (msg.final) { es.close(); es = null; }
  };
  es.onerror = () => { if (mine === seq && es) fail("连接中断，请重试"); };
}
function fail(message) {
  if (es) { es.close(); es = null; }
  document.body.classList.remove("loading");
  el.datasets.innerHTML = `<div class="panel"><div class="empty"><h3>出错了</h3><p>${esc(message)}</p></div></div>`;
}

/* ───────── 详情面板 ───────── */
function factsHTML(d) {
  const rows = [
    ["来源", (d.where || [d.source]).join("、")],
    ["发布方", d.publisher],
    ["许可", d.license ? esc(d.license) : '<span class="warn">许可未知（不代表可随意使用）</span>'],
    ["数据期", d.period],
    ["更新", d.updated],
    ["DOI", d.doi],
  ].filter(([, v]) => v);
  return `<dl class="facts-grid">${rows.map(([k, v]) => `<dt>${k}</dt><dd>${k === "许可" ? v : esc(v)}</dd>`).join("")}</dl>`;
}

function resourceHTML(r, i) {
  const meta = [r.kind === "api" ? "接口" : r.kind === "file" ? "文件" : r.kind, fmtSize(r.size_bytes)].filter(Boolean).join(" · ");
  return `<li class="res"><div class="res-top"><span class="res-name">${esc(r.name)}</span>${r.format ? `<span class="pill">${esc(r.format)}</span>` : ""}</div>
    <div class="res-meta">${esc(meta)}</div>
    ${r.note ? `<p class="res-note">${esc(r.note)}</p>` : ""}
    ${r.body ? `<pre class="code">${esc(r.body)}</pre>` : ""}
    <div class="res-actions">
      <a class="link-btn" href="${esc(safeUrl(r.url))}" target="_blank" rel="noopener noreferrer">${r.kind === "api" ? "打开接口地址" : "下载"} ${ICON_OUT}</a>
      <button class="link-btn" data-copy="${i}" type="button">复制链接</button>
      ${r.body ? `<button class="link-btn" data-copybody="${i}" type="button">复制请求体</button>` : ""}
    </div></li>`;
}

function renderSheet(d, resources, state) {
  let resBlock;
  if (state === "loading") resBlock = `<p class="sheet-note"><span class="spinner"></span>正在读取文件列表…</p>`;
  else if (resources.length) resBlock = `<ul class="res-list">${resources.map(resourceHTML).join("")}</ul>`;
  else {
    const canInspect = inspectOk && d.source !== "nbs";
    resBlock = `<p class="sheet-note">${state === "error" ? "读取失败。" : "上游没有公布文件或接口列表。"}不会编造列名或文件。${canInspect ? '<br><button class="link-btn" id="inspectBtn" type="button">尝试从页面读取文件列表</button>' : ""}</p>`;
  }
  el.sheetBody.innerHTML = `
    <h3 id="sheetTitle">${esc(d.title)}</h3>
    ${d.description ? `<p class="lead">${esc(d.description)}</p>` : ""}
    <a class="open" href="${esc(safeUrl(d.url))}" target="_blank" rel="noopener noreferrer">打开原站 ${ICON_OUT}</a>
    ${factsHTML(d)}
    <h4>数据资源</h4>
    ${resBlock}`;
  el.sheetBody._ctx = { d, resources };
}

function openSheet(d) {
  lastFocus = document.activeElement;
  renderSheet(d, d.resources || [], "ready");
  el.scrim.hidden = false; el.sheet.hidden = false;
  requestAnimationFrame(() => { el.scrim.classList.add("on"); el.sheet.classList.add("on"); });
  el.sheetClose.focus({ preventScroll: true });
  document.body.style.overflow = "hidden";
}
function closeSheet() {
  if (el.sheet.hidden) return;
  el.scrim.classList.remove("on"); el.sheet.classList.remove("on");
  document.body.style.overflow = "";
  setTimeout(() => { el.scrim.hidden = true; el.sheet.hidden = true; }, 420);
  lastFocus?.focus?.({ preventScroll: true });
}

async function inspect(d) {
  renderSheet(d, [], "loading");
  try {
    const r = await fetch("/api/inspect?url=" + encodeURIComponent(d.url));
    if (!r.ok) throw new Error((await r.json()).detail || r.statusText);
    const data = await r.json();
    d.resources = data.resources;
    renderSheet(d, data.resources, "ready");
  } catch (e) {
    renderSheet(d, [], "error");
    toast("读取失败：" + e.message);
  }
}

/* ───────── 事件 ───────── */
el.q.addEventListener("input", () => { el.go.disabled = !el.q.value.trim(); });
el.form.addEventListener("submit", (e) => { e.preventDefault(); run(el.q.value); el.q.blur(); });
el.examples.addEventListener("click", (e) => {
  const b = e.target.closest("[data-q]"); if (b) run(b.dataset.q);
});
el.datasets.addEventListener("click", (e) => {
  if (e.target.closest("a")) return;
  const row = e.target.closest(".row"); if (row) openSheet(currentRows[+row.dataset.i]);
});
el.datasets.addEventListener("keydown", (e) => {
  if (e.key !== "Enter" && e.key !== " ") return;
  const row = e.target.closest(".row");
  if (row && e.target === row) { e.preventDefault(); openSheet(currentRows[+row.dataset.i]); }
});
el.sheetBody.addEventListener("click", (e) => {
  const ctx = el.sheetBody._ctx; if (!ctx) return;
  const c = e.target.closest("[data-copy]"), n = e.target.closest("[data-copybody]");
  if (c) copy(ctx.resources[+c.dataset.copy].url);
  else if (n) copy(ctx.resources[+n.dataset.copybody].body);
  else if (e.target.closest("#inspectBtn")) inspect(ctx.d);
});
el.sheetClose.addEventListener("click", closeSheet);
el.scrim.addEventListener("click", closeSheet);
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") closeSheet();
  if (e.key === "/" && document.activeElement !== el.q && !/^(INPUT|TEXTAREA)$/.test(document.activeElement.tagName)) {
    e.preventDefault(); el.q.focus(); el.q.select();
  }
});
window.addEventListener("popstate", () => {
  const q = new URLSearchParams(location.search).get("q"); if (q) run(q);
});

/* ───────── 启动 ───────── */
loadStatus();
const initial = new URLSearchParams(location.search).get("q");
if (initial) run(initial); else el.q.focus();
