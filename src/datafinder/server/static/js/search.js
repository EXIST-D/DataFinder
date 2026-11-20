"use strict";
/* 搜索视图：门户（毫秒级）+ 数据集（SSE 逐批）+ 更多检索入口；点开数据集看详情。 */

import { $, api, copy, esc, extLink, fmtSize, ICON_OUT, portalBadges, safeUrl, setLoading, skeleton, emptyPanel, toast, verifyBadge } from "./util.js";
import { closeSheet, openSheet, sheetBody } from "./sheet.js";
import { updateUrl } from "./router.js";

const el = {
  form: $("searchForm"), q: $("q"), go: $("go"), examples: $("examples"), results: $("results"),
  sources: $("sources"), datasets: $("datasets"), count: $("count"), portals: $("portals"),
  footnote: $("footnote"), sideLinks: $("sideLinks"), moreLinks: $("moreLinks"),
};

const STATUS_TEXT = {
  pending: "查询中", ok: "已返回", cached: "来自缓存", stale: "用了过期缓存", skipped: "不适用",
  resting: "休息中", error: "出错", timeout: "超时", not_waited: "没等它",
};
// 结果页右侧"更多检索入口"只放最常用的几个，其余在"检索入口"页
const SIDE_LINK_IDS = ["dateno", "google-ds", "scidb", "hf", "kaggle", "zenodo", "europa", "bing-files"];

let es = null;
let seq = 0;
let lastSnap = null;
let shown = new Set();
let currentRows = [];
let currentQuery = "";
let inspectOk = false;

export function setInspectAvailable(v) { inspectOk = !!v; }

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
    d.region ? `<span>${esc(d.region)}</span>` : "",
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
      <a class="link-btn" href="${esc(safeUrl(d.open_url || d.url))}" target="_blank" rel="noopener noreferrer">打开原站 ${ICON_OUT}</a>
    </div>
  </li>`;
}

function renderDatasets(snap) {
  const ds = snap.datasets;
  currentRows = ds;
  el.count.textContent = ds.length ? `${ds.length} 条${snap.candidates > ds.length ? `（共 ${snap.candidates} 条候选）` : ""}` : "";
  if (!ds.length) {
    el.datasets.innerHTML = snap.final ? emptyPanel("没有找到匹配的数据集", "可以去掉格式词、换个说法，或先看右侧的相关门户与检索入口。") : skeleton();
    return;
  }
  el.datasets.innerHTML = `<ol class="panel">${ds.map(rowHTML).join("")}</ol>`;
  ds.forEach((d) => shown.add(d.url));
}

/* ───────── 渲染：门户 / 检索入口 ───────── */
function portalMini(p) {
  const place = [p.country, p.region].filter(Boolean).join("·");
  const sub = [place, p.org_type, p.type, p.industries.slice(0, 2).join(" ")].filter(Boolean).join(" · ");
  return `<a class="portal" href="${esc(safeUrl(p.url))}" target="_blank" rel="noopener noreferrer">
    <div class="portal-name">${esc(p.name)}<span class="portal-badges">${portalBadges(p)}${verifyBadge(p)}</span></div>
    <div class="portal-sub">${esc(sub)}</div></a>`;
}

function renderPortals(data) {
  if (!data.synced) { el.portals.innerHTML = emptyPanel("", "门户库还是空的。请在终端运行 datafinder sync 后刷新。"); return; }
  if (!data.portals.length) { el.portals.innerHTML = emptyPanel("", "没有匹配的门户"); return; }
  el.portals.innerHTML = `<div class="panel">${data.portals.map(portalMini).join('<div class="sep"></div>')}</div>`;
}

function renderSideLinks(data) {
  const flat = data.groups.flatMap((g) => g.links);
  const picked = SIDE_LINK_IDS.map((id) => flat.find((l) => l.id === id)).filter(Boolean);
  el.sideLinks.innerHTML = `<div class="panel">${picked.map((l) => `
    <a class="portal" href="${esc(safeUrl(l.url))}" target="_blank" rel="noopener noreferrer">
      <div class="portal-name">${esc(l.name)} <span class="link-out">${ICON_OUT}</span></div>
      <div class="portal-sub">${esc(l.scope || "")}${l.query ? ` · 搜“${esc(l.query)}”` : ""}</div></a>`).join('<div class="sep"></div>')}</div>`;
  el.moreLinks.href = "/?v=links&q=" + encodeURIComponent(currentQuery);
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
  if (snap.final) setLoading(false);
}

/* ───────── 检索 ───────── */
export function run(query, { syncUrl = true } = {}) {
  query = query.trim();
  if (!query) return;
  const mine = ++seq;
  currentQuery = query;
  if (es) { es.close(); es = null; }
  closeSheet();
  shown = new Set(); lastSnap = null; currentRows = [];
  el.q.value = query; el.go.disabled = false;
  document.body.classList.add("has-results");
  setLoading(true);
  el.results.hidden = false;
  el.sources.innerHTML = ""; el.count.textContent = ""; el.footnote.textContent = "";
  el.datasets.innerHTML = skeleton();
  el.portals.innerHTML = skeleton();
  el.sideLinks.innerHTML = "";
  if (syncUrl) updateUrl({ v: "", q: query, domain: "", country: "", region: "", tiers: "", kind: "", openness: "", api: "", official: "", sort: "", page: "", db: "" });
  document.title = `${query} · DataFinder`;

  api("/api/portals", { limit: 8, q: query })
    .then((d) => { if (mine === seq) renderPortals(d); })
    .catch(() => { if (mine === seq) el.portals.innerHTML = emptyPanel("", "门户加载失败"); });
  api("/api/links", { q: query })
    .then((d) => { if (mine === seq) renderSideLinks(d); })
    .catch(() => { /* 检索入口只是附加信息 */ });

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
  setLoading(false);
  el.datasets.innerHTML = emptyPanel("出错了", message);
}

/* ───────── 详情面板 ───────── */
function highlightsHTML(d) {
  const rows = (d.highlights || []).filter((h) => h.label !== "简介" && h.label !== "DOI");
  return `<dl class="facts-grid">${rows.map((h) => `
    <dt>${esc(h.label)}</dt>
    <dd${h.value.startsWith("许可未知") || h.value === "上游未标明" ? ' class="warn"' : ""}>${esc(h.value)}${h.note ? `<small>${esc(h.note)}</small>` : ""}</dd>`).join("")}</dl>`;
}

function resourceHTML(r, i) {
  const meta = [r.kind === "api" ? "接口" : r.kind === "file" ? "文件" : r.kind, fmtSize(r.size_bytes)].filter(Boolean).join(" · ");
  return `<li class="res"><div class="res-top"><span class="res-name">${esc(r.name)}</span>${r.format ? `<span class="pill">${esc(r.format)}</span>` : ""}</div>
    <div class="res-meta">${esc(meta)}</div>
    ${r.note ? `<p class="res-note">${esc(r.note)}</p>` : ""}
    ${r.body ? `<pre class="code">${esc(r.body)}</pre>` : ""}
    <div class="res-actions">
      ${extLink(r.url, r.kind === "api" ? "打开接口地址" : "下载")}
      <button class="link-btn" data-copy="${i}" type="button">复制链接</button>
      ${r.body ? `<button class="link-btn" data-copybody="${i}" type="button">复制请求体</button>` : ""}
    </div></li>`;
}

function renderDatasetSheet(d, resources, state) {
  let resBlock;
  if (state === "loading") resBlock = `<p class="sheet-note"><span class="spinner"></span>正在读取文件列表…</p>`;
  else if (resources.length) resBlock = `<ul class="res-list">${resources.map(resourceHTML).join("")}</ul>`;
  else {
    const canInspect = inspectOk && d.source !== "nbs";
    resBlock = `<p class="sheet-note">${state === "error" ? "读取失败。" : "上游没有公布文件或接口列表。"}不会编造列名或文件。${canInspect ? '<br><button class="link-btn" id="inspectBtn" type="button">尝试从页面读取文件列表</button>' : ""}</p>`;
  }
  const lead = (d.highlights || []).find((h) => h.label === "简介");
  const badges = [
    `<span class="pill">${esc((d.where || [d.source]).join("、"))}</span>`,
    d.portal?.org_type ? `<span class="pill ${d.portal.official ? "res-pill" : ""}">${esc(d.portal.org_type)}</span>` : "",
    d.region ? `<span class="pill">${esc(d.region)}</span>` : "",
  ].join("");
  const actions = [
    `<a class="open" href="${esc(safeUrl(d.open_url || d.url))}" target="_blank" rel="noopener noreferrer">打开原站 ${ICON_OUT}</a>`,
    d.doi_url ? extLink(d.doi_url, "DOI 链接") : "",
    d.portal ? extLink(d.portal.url, `所属门户：${d.portal.name}`) : "",
    `<a class="link-btn" data-view="links" href="/?v=links&q=${encodeURIComponent(d.title)}">在其他引擎继续搜 ›</a>`,
  ].join("");
  sheetBody.innerHTML = `
    <h3 id="sheetTitle">${esc(d.title)}</h3>
    <div class="sheet-badges">${badges}</div>
    <div class="sheet-actions">${actions}</div>
    <h4>要点</h4>
    ${highlightsHTML(d)}
    ${d.description ? `<h4>简介</h4><p class="lead">${esc(d.description)}</p>` : lead ? `<h4>简介</h4><p class="lead">${esc(lead.value)}</p>` : ""}
    <h4>数据资源</h4>
    ${resBlock}`;
  sheetBody._ctx = { d, resources };
}

function openDataset(d) {
  openSheet(() => renderDatasetSheet(d, d.resources || [], "ready"));
}

async function inspect(d) {
  renderDatasetSheet(d, [], "loading");
  try {
    const data = await api("/api/inspect", { url: d.url });
    d.resources = data.resources;
    renderDatasetSheet(d, data.resources, "ready");
  } catch (e) {
    renderDatasetSheet(d, [], "error");
    toast("读取失败：" + e.message);
  }
}

/* ───────── 事件 ───────── */
el.q.addEventListener("input", () => { el.go.disabled = !el.q.value.trim(); });
el.form.addEventListener("submit", (e) => { e.preventDefault(); run(el.q.value); el.q.blur(); });
el.examples.addEventListener("click", (e) => { const b = e.target.closest("[data-q]"); if (b) run(b.dataset.q); });
el.datasets.addEventListener("click", (e) => {
  if (e.target.closest("a")) return;
  const row = e.target.closest(".row");
  if (row) openDataset(currentRows[+row.dataset.i]);
});
el.datasets.addEventListener("keydown", (e) => {
  if (e.key !== "Enter" && e.key !== " ") return;
  const row = e.target.closest(".row");
  if (row && e.target === row) { e.preventDefault(); openDataset(currentRows[+row.dataset.i]); }
});
sheetBody.addEventListener("click", (e) => {
  const ctx = sheetBody._ctx;
  if (!ctx) return;
  if (e.target.closest("a[data-view]")) closeSheet(); // 站内跳转：先关面板
  const c = e.target.closest("[data-copy]");
  const n = e.target.closest("[data-copybody]");
  if (c) copy(ctx.resources[+c.dataset.copy].url);
  else if (n) copy(ctx.resources[+n.dataset.copybody].body);
  else if (e.target.closest("#inspectBtn")) inspect(ctx.d);
});
document.addEventListener("keydown", (e) => {
  if (e.key === "/" && document.body.dataset.view === "search" && document.activeElement !== el.q && !/^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement.tagName)) {
    e.preventDefault(); el.q.focus(); el.q.select();
  }
});

/* ───────── 视图入口 ───────── */
function reset() {
  seq++;
  if (es) { es.close(); es = null; }
  setLoading(false);
  currentQuery = ""; lastSnap = null; currentRows = []; shown = new Set();
  el.q.value = ""; el.go.disabled = true;
  el.results.hidden = true;
  document.body.classList.remove("has-results");
}

export function enter(params) {
  const q = (params.q || "").trim();
  if (q) {
    if (q !== currentQuery || !lastSnap) run(q, { syncUrl: false });
    else document.title = `${q} · DataFinder`;
  } else {
    document.title = "DataFinder · 找数据，一句话的事";
    if (currentQuery) reset(); // 点了"搜索"标签或品牌：回到首页的空白搜索框
    el.q.focus();
  }
}
