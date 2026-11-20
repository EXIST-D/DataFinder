"use strict";
/* 中国统计：国家统计局分类树（逐级加载）→ 指标 → 取数；31 个省级统计局；省市开放平台。 */

import { $, api, copy, emptyPanel, esc, extLink, ICON_CHEVRON, safeUrl, skeleton, toast, verifyBadge } from "./util.js";
import { openSheet, sheetBody } from "./sheet.js";
import { updateUrl } from "./router.js";

const el = {
  dbTabs: $("dbTabs"), tree: $("tree"), bureaus: $("bureaus"), bureauCount: $("bureauCount"), platforms: $("platforms"),
};
const NBS_PAGE = "https://data.stats.gov.cn/dg/website/page.html";
const DEFAULT_AREAS = ["北京市", "上海市", "广东省"];
const DTS_HINT = { YY: "2018YY-2025YY（年）", MM: "202401MM-202512MM（月）", SS: "202401SS-202504SS（季度，01–04）" };

let overview = null;
let db = null; // { code, name, dt_type }
let crumbs = []; // [{ id, name }]，第 0 个是数据库本身
let nodes = [];
let view = "tree"; // tree | indicators
let leaf = null; // 当前叶子数据集 { id, name, root_id }
let indicators = [];
let treeSeq = 0;

/* ───────── 概览：数据库、省级统计局、开放平台 ───────── */
function renderDbTabs() {
  el.dbTabs.innerHTML = overview.databases.map((d) => `
    <button type="button" class="chip db${db?.code === d.code ? " on" : ""}" role="tab" aria-selected="${db?.code === d.code}" data-db="${esc(d.code)}">${esc(d.name)}</button>`).join("");
}

function bureauCard(p) {
  const state = verifyBadge(p);
  return `<a class="bcard" href="${esc(safeUrl(p.url))}" target="_blank" rel="noopener noreferrer" title="${esc(p.note || p.url)}">
    <span class="bcard-name">${esc(p.name)}</span>
    <span class="bcard-sub">${esc(p.region || "")}${state ? ` ${state}` : ""}</span></a>`;
}

function renderBureaus() {
  el.bureaus.innerHTML = overview.bureaus.map(bureauCard).join("");
  const ok = overview.bureaus.filter((p) => p.verify === "ok").length;
  el.bureauCount.textContent = `${overview.bureaus.length} 个 · ${ok} 个已核验可访问`;
}

function renderPlatforms() {
  if (!overview.platforms.length) { el.platforms.innerHTML = emptyPanel("", "暂无"); return; }
  el.platforms.innerHTML = `<ol class="panel plist">${overview.platforms.map((p) => `
    <li class="prow">
      <div class="prow-main">
        <div class="prow-title">${esc(p.name)}<small>${esc([p.country, p.region].filter(Boolean).join("·"))}</small></div>
        ${p.note ? `<p class="prow-note">${esc(p.note)}</p>` : ""}
      </div>
      ${extLink(p.url, "打开", "link-btn go-out")}
    </li>`).join("")}</ol>`;
}

/* ───────── 分类树 ───────── */
function renderTree() {
  const crumbHtml = crumbs.map((c, i) => `<button type="button" class="crumb${i === crumbs.length - 1 && view === "tree" ? " cur" : ""}" data-crumb="${i}">${esc(c.name)}</button>`).join('<span class="crumb-sep">›</span>');
  let body = "";
  if (view === "tree") {
    body = nodes.length
      ? `<ul class="nodes">${nodes.map((n, i) => `
        <li><button type="button" class="node" data-node="${i}">
          <span class="node-name">${esc(n.name)}</span>
          ${n.is_leaf ? '<span class="pill res-pill">数据集</span>' : ""}
          ${n.sdate || n.edate ? `<span class="node-sub">${esc([n.sdate, n.edate].filter(Boolean).join(" – "))}</span>` : ""}
          <span class="node-go">${ICON_CHEVRON}</span></button></li>`).join("")}</ul>`
      : '<p class="empty-inline">这一层没有内容。</p>';
  } else {
    body = indicators.length
      ? `<ul class="nodes">${indicators.map((x, i) => `
        <li><button type="button" class="node" data-ind="${i}">
          <span class="node-name">${esc(x.name)}</span>
          ${x.unit ? `<span class="pill">${esc(x.unit)}</span>` : ""}
          <span class="node-go">${ICON_CHEVRON}</span></button></li>`).join("")}</ul>`
      : '<p class="empty-inline">该数据集下没有指标。</p>';
  }
  el.tree.innerHTML = `<div class="crumbs">${crumbHtml}</div>${body}`;
}

function treeError(e) {
  const hint = e.status === 429 ? "国家统计局的访问防护要求验证，已暂停访问。请等几分钟再点；这是对方站点的限流，不是你的网络问题。" : "";
  el.tree.hidden = false;
  el.tree.innerHTML = `<div class="crumbs">${crumbs.map((c) => esc(c.name)).join(" › ")}</div>
    <div class="empty"><h3>${e.status === 429 ? "暂时被限流" : "加载失败"}</h3><p>${esc(hint || e.message)}</p>
    <p><button type="button" class="chip" data-retry="1">重试</button></p></div>`;
}

async function loadChildren(pid) {
  const mine = ++treeSeq;
  el.tree.hidden = false;
  el.tree.innerHTML = skeleton(2);
  try {
    let res = await api("/api/nbs/tree", { code: db.code, pid });
    // 每个库只有一个根节点（就是数据库自己）：自动往下走一层
    if (!pid && res.nodes.length === 1 && !res.nodes[0].is_leaf) {
      crumbs = [{ id: res.nodes[0].id, name: db.name }];
      res = await api("/api/nbs/tree", { code: db.code, pid: res.nodes[0].id });
    }
    if (mine !== treeSeq) return;
    nodes = res.nodes; view = "tree"; leaf = null;
    renderTree();
  } catch (e) {
    if (mine === treeSeq) treeError(e);
  }
}

async function openDb(code, { push = false } = {}) {
  db = overview.databases.find((d) => d.code === code);
  if (!db) return;
  crumbs = [];
  updateUrl({ v: "china", db: code }, { push });
  renderDbTabs();
  await loadChildren("");
}

async function openNode(n) {
  if (!n.is_leaf) {
    crumbs.push({ id: n.id, name: n.name });
    await loadChildren(n.id);
    return;
  }
  const mine = ++treeSeq;
  crumbs.push({ id: n.id, name: n.name });
  leaf = n;
  el.tree.innerHTML = skeleton(2);
  try {
    const res = await api("/api/nbs/indicators", { cid: n.id });
    if (mine !== treeSeq) return;
    indicators = res.indicators; view = "indicators";
    renderTree();
  } catch (e) {
    if (mine === treeSeq) { crumbs.pop(); treeError(e); }
  }
}

async function gotoCrumb(i) {
  const target = crumbs[i];
  crumbs = crumbs.slice(0, i + 1);
  if (i === 0 && crumbs.length === 1 && crumbs[0].id) { await loadChildren(crumbs[0].id); return; }
  await loadChildren(target.id);
}

/* ───────── 指标详情：口径、地区、取数 ───────── */
const pathText = () => crumbs.map((c) => c.name).join(" › ");

function indicatorSheet(ind) {
  openSheet(() => {
    sheetBody.innerHTML = `
      <h3 id="sheetTitle">${esc(ind.name)}</h3>
      <div class="sheet-badges"><span class="pill">国家统计局</span><span class="pill res-pill">官方</span>${ind.unit ? `<span class="pill">${esc(ind.unit)}</span>` : ""}</div>
      <div class="sheet-actions">${extLink(NBS_PAGE, "打开国家数据官网")}</div>
      <p class="sheet-note">在官网按此路径查找：<strong>${esc(pathText())}</strong>（单页应用不支持指标深链接）。</p>
      ${ind.mark ? `<h4>统计口径</h4><p class="lead">${esc(ind.mark)}</p>` : ""}
      <h4>地区与取数</h4>
      <div id="nbsControls"><p class="sheet-note"><span class="spinner"></span>正在读取可选地区…</p></div>
      <div id="nbsResult"></div>
      <h4>接口请求体</h4>
      <p class="sheet-note">POST ${esc("https://data.stats.gov.cn/dg/website/publicrelease/web/external/stream/esData")}，需带 Referer 与 <code>Content-Type: application/json</code>。多地区用 <code>showType "3"</code>。<code>dts</code> 的写法（实测）：${esc(DTS_HINT[db.dt_type] || "")}；<code>LAST5</code> 这类写法不支持。</p>
      <pre class="code" id="nbsBody"></pre>
      <div class="res-actions"><button class="link-btn" id="copyNbsBody" type="button">复制请求体</button></div>`;
    sheetBody._nbs = { ind, selected: new Set(), regions: [], n: 8 };
    loadRegions();
  });
}

function bodyJson(ctx) {
  const das = ctx.regions.filter((r) => ctx.selected.has(r.value)).map((r) => ({ text: r.text, value: r.value }));
  return JSON.stringify({
    cid: leaf.id, indicatorIds: [ctx.ind.id], daCatalogId: "", das, showType: "3",
    dts: [`<起>-<止>`], rootId: leaf.root_id,
  }, null, 1);
}

function refreshBody() {
  const ctx = sheetBody._nbs;
  const pre = $("nbsBody");
  if (pre) pre.textContent = bodyJson(ctx);
}

async function loadRegions() {
  const ctx = sheetBody._nbs;
  const box = $("nbsControls");
  try {
    const res = await api("/api/nbs/regions", { cid: leaf.id });
    ctx.regions = res.regions;
    const pre = ctx.regions.filter((r) => DEFAULT_AREAS.includes(r.text));
    (pre.length ? pre : ctx.regions.slice(0, 1)).forEach((r) => ctx.selected.add(r.value));
    drawControls(box);
    refreshBody();
  } catch (e) {
    box.innerHTML = `<p class="sheet-note">${esc(e.status === 429 ? "国家统计局暂时限流，请稍后再试。" : "读取地区失败：" + e.message)}</p>`;
  }
}

function drawControls(box) {
  const ctx = sheetBody._nbs;
  if (ctx.regions.length === 1) {
    box.innerHTML = `<p class="sheet-note">该数据集只有「${esc(ctx.regions[0].text)}」口径，不分地区。</p>
      <div class="ctl-row">
        <label class="sort">取最近 <select id="nbsN">${[5, 8, 12, 20].map((n) => `<option value="${n}"${n === ctx.n ? " selected" : ""}>${n} 期</option>`).join("")}</select></label>
        <button type="button" class="primary" id="nbsGo">查看数据</button>
      </div>`;
    return;
  }
  box.innerHTML = `
    <div class="chips-wrap" id="areaChips">${ctx.regions.map((r) => `
      <button type="button" class="chip sm${ctx.selected.has(r.value) ? " on" : ""}" data-area="${esc(r.value)}" aria-pressed="${ctx.selected.has(r.value)}">${esc(r.text)}</button>`).join("")}</div>
    <p class="sheet-note">最多选 8 个地区，已选 ${ctx.selected.size} 个。</p>
    <div class="ctl-row">
      <label class="sort">取最近 <select id="nbsN">${[5, 8, 12, 20].map((n) => `<option value="${n}"${n === ctx.n ? " selected" : ""}>${n} 期</option>`).join("")}</select></label>
      <button type="button" class="primary" id="nbsGo">查看数据</button>
    </div>`;
}

async function fetchSeries() {
  const ctx = sheetBody._nbs;
  const out = $("nbsResult");
  const areas = [...ctx.selected];
  if (!areas.length) { out.innerHTML = '<p class="sheet-note">请先选择至少一个地区。</p>'; return; }
  out.innerHTML = '<p class="sheet-note"><span class="spinner"></span>正在取数…</p>';
  try {
    const res = await api("/api/nbs/series", { code: db.code, cid: leaf.id, ind: ctx.ind.id, root: leaf.root_id, areas: areas.join(","), n: ctx.n });
    ctx.series = res;
    if (!res.periods.length) { out.innerHTML = '<p class="sheet-note">这些地区在所选时间范围内没有已发布的数据。</p>'; return; }
    const head = `<tr><th>期别</th>${res.areas.map((a) => `<th>${esc(a)}</th>`).join("")}</tr>`;
    const rows = res.periods.map((p) => `<tr><th>${esc(p.name)}</th>${res.areas.map((a) => `<td>${esc(p.values[a] ?? "—")}</td>`).join("")}</tr>`).join("");
    out.innerHTML = `<div class="table-wrap"><table class="data"><caption>${esc(captionOf(res, ctx.ind))}</caption><thead>${head}</thead><tbody>${rows}</tbody></table></div>
      <div class="res-actions"><button class="link-btn" id="nbsCsv" type="button">复制为 CSV</button></div>
      <p class="sheet-note">数据来自国家统计局发布库的实时接口；数值版权归国家统计局，请以官网为准。</p>`;
  } catch (e) {
    out.innerHTML = `<p class="sheet-note">${esc(e.status === 429 ? "国家统计局暂时限流（访问防护），请几分钟后再试。" : "取数失败：" + e.message)}</p>`;
  }
}

function captionOf(res, ind) {
  const name = (res.indicator || ind.name).trim();
  return res.unit && !name.includes(res.unit) ? `${name}（${res.unit}）` : name;
}

function csvOf(series) {
  const esc2 = (s) => `"${String(s).replaceAll('"', '""')}"`;
  const lines = [["期别", ...series.areas].map(esc2).join(",")];
  for (const p of series.periods) lines.push([p.name, ...series.areas.map((a) => p.values[a] ?? "")].map(esc2).join(","));
  return lines.join("\n");
}

/* ───────── 事件 ───────── */
el.dbTabs.addEventListener("click", (e) => { const b = e.target.closest("[data-db]"); if (b) openDb(b.dataset.db, { push: true }); });
el.tree.addEventListener("click", (e) => {
  if (e.target.closest("[data-retry]")) { (crumbs.length ? loadChildren(crumbs[crumbs.length - 1].id) : loadChildren("")); return; }
  const c = e.target.closest("[data-crumb]");
  if (c) { gotoCrumb(Number(c.dataset.crumb)); return; }
  const n = e.target.closest("[data-node]");
  if (n) { openNode(nodes[Number(n.dataset.node)]); return; }
  const i = e.target.closest("[data-ind]");
  if (i) indicatorSheet(indicators[Number(i.dataset.ind)]);
});
sheetBody.addEventListener("click", (e) => {
  const ctx = sheetBody._nbs;
  if (!ctx) return;
  const area = e.target.closest("[data-area]");
  if (area) {
    const v = area.dataset.area;
    if (ctx.selected.has(v)) ctx.selected.delete(v);
    else if (ctx.selected.size >= 8) { toast("最多选 8 个地区"); return; }
    else ctx.selected.add(v);
    drawControls($("nbsControls")); refreshBody();
    return;
  }
  if (e.target.closest("#nbsGo")) { fetchSeries(); return; }
  if (e.target.closest("#nbsCsv") && ctx.series) { copy(csvOf(ctx.series)); return; }
  if (e.target.closest("#copyNbsBody")) copy($("nbsBody").textContent);
});
sheetBody.addEventListener("change", (e) => {
  const ctx = sheetBody._nbs;
  if (ctx && e.target.id === "nbsN") ctx.n = Number(e.target.value);
});

/* ───────── 视图入口 ───────── */
export async function enter(params) {
  document.title = "中国统计 · DataFinder";
  if (!overview) {
    el.bureaus.innerHTML = skeleton(2);
    try {
      overview = await api("/api/china/overview");
    } catch (e) {
      el.bureaus.innerHTML = emptyPanel("加载失败", e.message);
      return;
    }
    renderBureaus(); renderPlatforms(); renderDbTabs();
    if (!overview.synced) el.platforms.innerHTML = emptyPanel("门户库还是空的", "请在终端运行 datafinder sync 后刷新页面。");
  }
  if (params.db && params.db !== db?.code) openDb(params.db);
}
