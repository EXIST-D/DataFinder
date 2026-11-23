"use strict";
/* 数据来源：已建立资源索引的来源/门户，以及它们"接入到什么程度"。全部门户请到"门户目录"。 */

import { $, api, emptyPanel, esc, extLink, skeleton } from "./util.js";
import { currentParams, go, updateUrl } from "./router.js";
import { openSheet, sheetBody } from "./sheet.js";

const el = { form: $("sForm"), q: $("sQ"), levels: $("sLevels"), total: $("sTotal"), list: $("sList"), pager: $("sPager") };
const PAGE = 20;
const LEVELS = [["", "全部"], ["indexed_and_fetchable", "可取数"], ["indexed", "已有资源目录"]];
const CAP = { fetch: "可实时取数", download: "有文件直链", discover: "可检索" };
let state = { q: "", level: "", page: 1 };
let seq = 0;
let current = [];

const syncUrl = () => updateUrl({ v: "sources", q: state.q, level: state.level, page: state.page > 1 ? state.page : "" });
function change(patch) { state = { ...state, ...patch, page: patch.page ?? 1 }; syncUrl(); load(); }

const badge = (e) => `<span class="badge${e.integration.level === "indexed_and_fetchable" ? " dl-generate" : ""}">${esc(e.integration.level_zh)}</span>`;
const caps = (e) => e.integration.capabilities.filter((c) => CAP[c]).map((c) => `<span class="pill">${CAP[c]}</span>`).join("");

function row(e, i) {
  const n = e.integration.indexed_resources;
  return `<li class="prow clickable" data-i="${i}" tabindex="0" role="button"><div class="prow-main">
    <div class="prow-title">${esc(e.name)}<span class="portal-badges">${badge(e)}</span></div>
    <div class="facts"><span>${n.toLocaleString("zh-CN")} 条已索引资源</span>${e.last_indexed_at ? `<span>最近索引 ${esc(e.last_indexed_at.slice(0, 10))}</span>` : ""}${e.home_url ? `<span>${esc(e.home_url)}</span>` : ""}</div>
    <div class="row-foot">${caps(e)}</div></div></li>`;
}

async function load() {
  const mine = ++seq;
  el.q.value = state.q;
  el.levels.innerHTML = LEVELS.map(([v, t]) => `<button type="button" class="chip${state.level === v ? " on" : ""}" data-level="${v}">${t}</button>`).join("");
  el.list.innerHTML = skeleton(4);
  try {
    const r = await api("/api/data-sources", { scope: "indexed", q: state.q, level: state.level, page: state.page, size: PAGE });
    if (mine !== seq) return;
    current = r.items;
    el.total.textContent = `${r.total.toLocaleString("zh-CN")} 个来源`;
    el.list.innerHTML = r.items.length ? `<ol class="panel plist">${r.items.map(row).join("")}</ol><p class="note">${esc(r.note)}</p>` : `<div class="panel"><div class="empty"><h3>没有符合条件的来源</h3></div></div>`;
    const pages = Math.max(1, Math.ceil(r.total / PAGE));
    el.pager.innerHTML = pages > 1 ? `<button type="button" data-page="${state.page - 1}"${state.page === 1 ? " disabled" : ""}>上一页</button><span class="gap">${state.page} / ${pages}</span><button type="button" data-page="${state.page + 1}"${state.page === pages ? " disabled" : ""}>下一页</button>` : "";
  } catch (e) {
    if (mine !== seq) return;
    el.list.innerHTML = emptyPanel(e.status === 503 ? "资源目录还没建立" : "加载失败", e.message);
  }
}

export async function openDetail(id) {
  openSheet((b) => { b.innerHTML = '<p class="sheet-note"><span class="spinner"></span>读取中…</p>'; });
  try {
    const d = await api("/api/data-sources/detail", { id });
    const srcs = d.sources.map((s) => `<li><strong>${esc(s.name)}</strong> <span class="pill">${esc(s.source_id)}</span> ${s.resources.toLocaleString("zh-CN")} 条
      <div class="row-foot">${s.roots.map((r) => `<button type="button" class="tnode-go" data-node="${esc(r.id)}" data-source="${esc(s.source_id)}">${esc(r.name)}（${r.count.toLocaleString("zh-CN")}）</button>`).join("")}</div></li>`).join("");
    const related = d.related.map((r) => `<li><span class="pill">${esc(r.source_id)}</span> 相关（未计入接入）</li>`).join("");
    const nav = d.catalog_navigation;
    openSheet((b) => {
      b.innerHTML = `<h3 id="sheetTitle">${esc(d.name)}</h3>
        <div class="sheet-badges">${badge(d)}${caps(d)}</div>
        <dl class="facts-grid"><dt>官网</dt><dd>${d.home_url ? extLink(d.home_url, d.home_url) : "—"}</dd>
          <dt>已索引资源</dt><dd>${d.integration.indexed_resources.toLocaleString("zh-CN")} 条<small>来自本机目录，不等于原站全部数据</small></dd>
          ${d.last_indexed_at ? `<dt>最近索引</dt><dd>${esc(d.last_indexed_at.slice(0, 10))}</dd>` : ""}</dl>
        <h4>使用说明</h4><ul class="plain">${d.usage.map((u) => `<li>${esc(u)}</li>`).join("")}</ul>
        ${srcs ? `<h4>关联的来源与根目录</h4><ul class="plain">${srcs}</ul>` : ""}
        ${related ? `<h4>相关来源</h4><ul class="plain">${related}</ul>` : ""}
        ${nav ? `<div class="res-actions"><button class="link-btn" type="button" data-catalog="${esc(d.integration.linked_source_ids.join(","))}">在资源目录里看这些资源 ›</button> <button class="link-btn" type="button" data-tree="${esc(nav.node)}">在目录树中展开 ›</button></div>` : ""}`;
    });
  } catch (e) {
    openSheet((b) => { b.innerHTML = `<h3 id="sheetTitle">读取失败</h3><p class="sheet-note">${esc(e.message)}</p>`; });
  }
}

el.form.addEventListener("submit", (e) => { e.preventDefault(); change({ q: el.q.value.trim() }); });
el.levels.addEventListener("click", (e) => { const b = e.target.closest("[data-level]"); if (b) change({ level: b.dataset.level }); });
el.pager.addEventListener("click", (e) => { const p = e.target.closest("[data-page]"); if (p && !p.disabled) { state.page = Number(p.dataset.page); syncUrl(); load(); } });
el.list.addEventListener("click", (e) => { const r = e.target.closest("[data-i]"); if (r) openDetail(current[Number(r.dataset.i)].id); });
el.list.addEventListener("keydown", (e) => { if (e.key === "Enter") e.target.closest("[data-i]")?.click(); });
sheetBody.addEventListener("click", (e) => {
  const c = e.target.closest("[data-catalog]");
  if (c) return go("catalog", { source: c.dataset.catalog });
  const t = e.target.closest("[data-tree]");
  if (t) return go("tree", { axis: "source", node: t.dataset.tree });
  const n = e.target.closest("[data-node]");
  if (n) go("tree", { axis: "source", node: n.dataset.node });
});

export function enter() {
  document.title = "数据来源 · DataFinder";
  const p = currentParams();
  state = { q: p.q || "", level: LEVELS.some(([v]) => v === p.level) ? p.level || "" : "", page: Math.max(1, parseInt(p.page || "1", 10) || 1) };
  load();
}
