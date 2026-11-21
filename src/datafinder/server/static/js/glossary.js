"use strict";
/* 术语词典：官方原文定义 + 出处 + 容易混淆的概念。可以从术语一步跳到"查找相关数据"。 */

import { $, api, emptyPanel, esc, extLink, skeleton } from "./util.js";
import { currentParams, go, updateUrl } from "./router.js";
import { openSheet, sheetBody } from "./sheet.js";

const el = { form: $("gForm"), q: $("gQ"), topics: $("gTopics"), total: $("gTotal"), list: $("gList"), pager: $("gPager") };
const PAGE = 20;
let state = { q: "", topic: "", page: 1 };
let seq = 0;
let current = [];

const syncUrl = () => updateUrl({ v: "glossary", q: state.q, topic: state.topic, page: state.page > 1 ? state.page : "" });
function change(patch) { state = { ...state, ...patch, page: patch.page ?? 1 }; syncUrl(); load(); }

const status = (t) => `<span class="badge${t.verification_status === "draft" ? " dl-link" : ""}">${esc(t.status_zh)}</span>`;

function row(t, i) {
  const topics = t.topics.map((x) => `<span class="pill">${esc(x.name)}</span>`).join("");
  return `<li class="prow clickable" data-i="${i}" tabindex="0" role="button"><div class="prow-main">
    <div class="prow-title">${esc(t.name_zh)}${t.aliases.length ? `<small>${esc(t.aliases.join("、"))}</small>` : ""}<span class="portal-badges">${status(t)}</span></div>
    <p class="desc">${esc(t.definition)}</p><div class="row-foot">${topics}</div></div></li>`;
}

async function load() {
  const mine = ++seq;
  el.q.value = state.q;
  el.list.innerHTML = skeleton(4);
  try {
    const r = await api("/api/glossary", { q: state.q, topic: state.topic, limit: PAGE, offset: (state.page - 1) * PAGE });
    if (mine !== seq) return;
    current = r.items;
    el.topics.innerHTML = [`<button type="button" class="chip${state.topic ? "" : " on"}" data-topic="">全部</button>`, ...r.topics.map((t) => `<button type="button" class="chip${state.topic === t.id ? " on" : ""}" data-topic="${esc(t.id)}">${esc(t.name)} ${t.count}</button>`)].join("");
    el.total.textContent = `${r.total.toLocaleString("zh-CN")} 个术语`;
    el.list.innerHTML = r.items.length ? `<ol class="panel plist">${r.items.map(row).join("")}</ol><p class="note">${esc(r.note)}</p>` : `<div class="panel"><div class="empty"><h3>没有找到这个术语</h3><p>可以换个说法，或直接到资源目录检索。</p></div></div>`;
    const pages = Math.max(1, Math.ceil(r.total / PAGE));
    el.pager.innerHTML = pages > 1 ? `<button type="button" data-page="${state.page - 1}"${state.page === 1 ? " disabled" : ""}>上一页</button><span class="gap">${state.page} / ${pages}</span><button type="button" data-page="${state.page + 1}"${state.page === pages ? " disabled" : ""}>下一页</button>` : "";
  } catch (e) {
    if (mine !== seq) return;
    el.list.innerHTML = emptyPanel("加载失败", e.message);
  }
}

async function openDetail(id) {
  openSheet((b) => { b.innerHTML = '<p class="sheet-note"><span class="spinner"></span>读取中…</p>'; });
  try {
    const d = await api("/api/glossary/detail", { id });
    const rels = d.relations.map((r) => `<li class="clickable" data-term="${esc(r.term_id)}" tabindex="0" role="button"><span class="pill${r.type === "not_equivalent" ? " res-pill" : ""}">${esc(r.type_zh)}</span> <strong>${esc(r.name_zh)}</strong>${r.note ? `<small>${esc(r.note)}</small>` : ""}</li>`).join("");
    const same = d.same_name_terms.map((s) => `<li class="clickable" data-term="${esc(s.term_id)}" tabindex="0" role="button">${esc(s.source_name || s.term_id)}</li>`).join("");
    openSheet((b) => {
      b.innerHTML = `<h3 id="sheetTitle">${esc(d.name_zh)}</h3>
        <div class="sheet-badges">${status(d)}${d.topics.map((x) => `<span class="pill">${esc(x.name)}</span>`).join("")}${d.aliases.map((a) => `<span class="pill">${esc(a)}</span>`).join("")}</div>
        ${d.warning ? `<p class="prow-note">${esc(d.warning)}</p>` : ""}
        <h4>定义</h4><p class="defn">${esc(d.definition)}</p>
        ${d.scope_note ? `<p class="sheet-note">${esc(d.scope_note)}</p>` : ""}${d.note ? `<p class="sheet-note">${esc(d.note)}</p>` : ""}
        <dl class="facts-grid"><dt>出处</dt><dd>${d.source_url ? extLink(d.source_url, d.source_name || d.source_url) : "—"}${d.retrieved_at ? `<small>摘录日期 ${esc(d.retrieved_at)}</small>` : ""}</dd></dl>
        ${rels ? `<h4>相关概念</h4><ul class="plain">${rels}</ul>` : ""}
        ${same ? `<h4>其他页面里的同名术语</h4><ul class="plain">${same}</ul>` : ""}
        <div class="res-actions"><button class="link-btn" type="button" data-find="${esc(d.find_data.query)}">查找相关数据 ›</button></div>`;
    });
  } catch (e) {
    openSheet((b) => { b.innerHTML = `<h3 id="sheetTitle">读取失败</h3><p class="sheet-note">${esc(e.message)}</p>`; });
  }
}

el.form.addEventListener("submit", (e) => { e.preventDefault(); change({ q: el.q.value.trim() }); });
el.topics.addEventListener("click", (e) => { const b = e.target.closest("[data-topic]"); if (b) change({ topic: b.dataset.topic }); });
el.pager.addEventListener("click", (e) => { const p = e.target.closest("[data-page]"); if (p && !p.disabled) { state.page = Number(p.dataset.page); syncUrl(); load(); } });
el.list.addEventListener("click", (e) => { const r = e.target.closest("[data-i]"); if (r) openDetail(current[Number(r.dataset.i)].term_id); });
el.list.addEventListener("keydown", (e) => { if (e.key === "Enter") e.target.closest("[data-i]")?.click(); });
sheetBody.addEventListener("click", (e) => {
  const t = e.target.closest("[data-term]");
  if (t) return openDetail(t.dataset.term);
  const f = e.target.closest("[data-find]");
  if (f) go("catalog", { q: f.dataset.find });
});
sheetBody.addEventListener("keydown", (e) => { if (e.key === "Enter") e.target.closest("[data-term]")?.click(); });

export function enter() {
  document.title = "术语词典 · DataFinder";
  const p = currentParams();
  state = { q: p.q || "", topic: p.topic || "", page: Math.max(1, parseInt(p.page || "1", 10) || 1) };
  load();
}
