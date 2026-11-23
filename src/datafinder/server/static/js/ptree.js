"use strict";
/* 门户树：全部数据门户 国家 → 地区 → 机构类型 → 门户，已接入的门户还能一路点开到来源与具体资源。
   节点按需加载（一次一层），可键盘操作；展开状态记在本标签页里，刷新后恢复。 */

import { $, api, esc, extLink, safeUrl } from "./util.js";
import { currentParams, updateUrl } from "./router.js";
import { openDetail as openSourceDetail } from "./sources.js";
import { openDetail as openResourceDetail } from "./catalog.js";

const el = { form: $("ptForm"), q: $("ptQ"), tree: $("ptTree"), chips: $("ptChips"), results: $("ptResults"), note: $("ptNote"), collapse: $("ptCollapse") };
const PAGE = 100;
const STORE = "datafinder.ptree.open";
const LEVEL_CLS = { indexed_and_fetchable: " dl-generate", indexed: "", directory_only: " dl-link" };
const ICON = {
  folder: '<svg class="pt-ico" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round" aria-hidden="true"><path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/></svg>',
  portal: '<svg class="pt-ico" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c3 3.5 3 14.5 0 18M12 3c-3 3.5-3 14.5 0 18"/></svg>',
  source: '<svg class="pt-ico" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v6c0 1.7 3.6 3 8 3s8-1.3 8-3V5M4 11v6c0 1.7 3.6 3 8 3s8-1.3 8-3v-6"/></svg>',
  doc: '<svg class="pt-ico" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round" aria-hidden="true"><path d="M6 3h8l4 4v14H6z"/><path d="M14 3v4h4"/></svg>',
};
const CARET = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M9 6l6 6-6 6"/></svg>';
const iconOf = (n) => (n.kind === "portal" ? ICON.portal : n.kind === "source" ? ICON.source : n.kind === "resource" ? ICON.doc : ICON.folder);

let indexedOnly = false;
let results = [];
let seq = 0;
const nodes = new Map(); // id -> 节点数据（含已加载数量）

const saved = () => { try { return JSON.parse(sessionStorage.getItem(STORE) || "[]"); } catch { return []; } };
const remember = (ids) => { try { sessionStorage.setItem(STORE, JSON.stringify(ids.slice(-400))); } catch { /* 无痕模式等 */ } };
function setOpen(id, on) {
  const cur = saved().filter((x) => x !== id);
  remember(on ? [...cur, id] : cur);
}

/* ───────── 渲染 ───────── */
function badges(n) {
  const out = [];
  if (n.kind === "portal") {
    out.push(`<span class="badge${LEVEL_CLS[n.level] || ""}">${esc(n.level_zh)}</span>`);
    if (n.official) out.push('<span class="pill">官方</span>');
    if (n.api) out.push('<span class="pill">API</span>');
    if (n.indexed_resources) out.push(`<span class="pill res-pill">${n.indexed_resources.toLocaleString("zh-CN")} 条资源</span>`);
    if (n.verify === "blocked") out.push('<span class="pill">拒绝自动访问</span>');
  } else if (n.kind === "country" && n.indexed) {
    out.push(`<span class="pill res-pill">${n.indexed} 个已接入</span>`);
  } else if (n.kind === "source" && n.capabilities?.includes("fetch")) {
    out.push('<span class="badge dl-generate">可取数</span>');
  } else if (n.kind === "resource" || n.kind === "container") {
    if (n.resource_type) out.push(`<span class="pill">${esc(n.resource_type)}</span>`);
  }
  return out.join("");
}

function actions(n) {
  const a = [];
  if (n.kind === "portal") {
    a.push(`<button type="button" class="tnode-go" data-act="detail">详情</button>`);
    if (n.home_url) a.push(`<a class="tnode-go" href="${esc(safeUrl(n.home_url))}" target="_blank" rel="noopener noreferrer">官网 ↗</a>`);
  } else if (n.kind === "source") {
    a.push(`<button type="button" class="tnode-go" data-act="detail">详情</button>`);
  } else if (n.kind === "resource") {
    a.push(`<button type="button" class="tnode-go" data-act="open">查看</button>`);
  }
  return a.join("");
}

function nodeHtml(n) {
  nodes.set(n.id, { ...n, loaded: 0, total: 0 });
  const count = n.count != null ? `<span class="pt-n">${n.count.toLocaleString("zh-CN")}</span>` : "";
  const sub = n.kind === "portal" && n.org_type ? `<small>${esc([n.org_type, n.data_kind].filter(Boolean).join(" · "))}</small>` : "";
  const caret = n.has_children ? `<span class="pt-caret">${CARET}</span>` : '<span class="pt-caret none"></span>';
  return `<li class="pt-node" role="treeitem" data-id="${esc(n.id)}" data-kind="${esc(n.kind)}"${n.has_children ? ' aria-expanded="false"' : ""}>
    <div class="pt-row" tabindex="-1">${caret}${iconOf(n)}<span class="pt-name">${esc(n.name)}${sub}</span>${badges(n)}${count}<span class="pt-actions">${actions(n)}</span></div>
    ${n.has_children ? '<ul class="pt-kids" role="group" hidden></ul>' : ""}</li>`;
}

const kidsOf = (li) => li.querySelector(":scope > .pt-kids");
const isOpen = (li) => li.getAttribute("aria-expanded") === "true";

async function load(li, { more = false, focus = "" } = {}) {
  const id = li?.dataset.id ?? "";
  const st = id ? nodes.get(id) : { loaded: 0 };
  const kids = li ? kidsOf(li) : el.tree.querySelector(":scope > .pt-kids");
  const mine = ++seq;
  if (!more) { kids.innerHTML = '<li class="pt-msg"><span class="spinner"></span>读取中…</li>'; st.loaded = 0; }
  else kids.querySelector(".pt-more-li")?.remove();
  try {
    const r = await api("/api/portal-tree", { node: id, limit: id ? PAGE : 300, offset: more ? st.loaded : 0, indexed: indexedOnly, focus });
    if (!more) kids.innerHTML = "";
    kids.insertAdjacentHTML("beforeend", r.children.map(nodeHtml).join(""));
    st.loaded = (more ? st.loaded : r.offset) + r.children.length;
    st.total = r.total_children;
    if (st.loaded < st.total) kids.insertAdjacentHTML("beforeend", `<li class="pt-more-li"><button type="button" class="tree-more" data-more="${esc(id)}">再显示 ${Math.min(PAGE, st.total - st.loaded)} 项（共 ${st.total.toLocaleString("zh-CN")}）</button></li>`);
    if (!r.children.length && !more) kids.innerHTML = '<li class="pt-msg">这里没有更多节点</li>';
    if (!id) el.note.textContent = `共 ${r.total_children} 个国家/地区，${r.children.reduce((s, c) => s + (c.count || 0), 0).toLocaleString("zh-CN")} 个${indexedOnly ? "已接入的来源与门户" : "门户"}。点开一层看下一层；已接入的门户可继续点到来源与具体资源。`;
    return true;
  } catch (e) {
    kids.innerHTML = `<li class="pt-msg warn">${esc(e.status === 503 ? "门户库还没建立：先运行 datafinder sync" : e.message)}</li>`;
    return false;
  }
}

async function expand(li, opts = {}) {
  if (!li || li.getAttribute("aria-expanded") === null) return;
  const st = nodes.get(li.dataset.id);
  li.setAttribute("aria-expanded", "true");
  kidsOf(li).hidden = false;
  if (!st.loaded || opts.focus) await load(li, opts);
  setOpen(li.dataset.id, true);
}

function collapse(li) {
  if (!li || li.getAttribute("aria-expanded") === null) return;
  li.setAttribute("aria-expanded", "false");
  kidsOf(li).hidden = true;
  setOpen(li.dataset.id, false);
}

const toggle = (li) => (isOpen(li) ? collapse(li) : expand(li));

/* ───────── 交互 ───────── */
el.tree.addEventListener("click", (e) => {
  const li = e.target.closest(".pt-node");
  const more = e.target.closest("[data-more]");
  if (more) { const host = more.dataset.more ? el.tree.querySelector(`.pt-node[data-id="${CSS.escape(more.dataset.more)}"]`) : null; load(host, { more: true }); return; }
  if (!li) return;
  const n = nodes.get(li.dataset.id);
  const act = e.target.closest("[data-act]");
  if (e.target.closest("a")) return;
  if (act?.dataset.act === "detail") return openSourceDetail(n.portal_id || (n.source_id ? `source:${n.source_id}` : n.id));
  if (act?.dataset.act === "open" || (!li.hasAttribute("aria-expanded") && n.kind === "resource")) return n.resource_id && openResourceDetail(n.resource_id);
  if (e.target.closest(".pt-row") && li.hasAttribute("aria-expanded")) toggle(li);
});

function visibleRows() { return [...el.tree.querySelectorAll(".pt-row")].filter((r) => !r.closest(".pt-kids[hidden]")); }
function focusRow(row) { if (!row) return; el.tree.querySelectorAll(".pt-row[tabindex='0']").forEach((r) => r.tabIndex = -1); row.tabIndex = 0; row.focus(); }

el.tree.addEventListener("keydown", (e) => {
  const row = e.target.closest(".pt-row");
  if (!row) return;
  const li = row.closest(".pt-node");
  const rows = visibleRows();
  const i = rows.indexOf(row);
  if (e.key === "ArrowDown") { e.preventDefault(); focusRow(rows[i + 1]); }
  else if (e.key === "ArrowUp") { e.preventDefault(); focusRow(rows[i - 1]); }
  else if (e.key === "ArrowRight") { e.preventDefault(); if (li.hasAttribute("aria-expanded")) { if (!isOpen(li)) expand(li); else focusRow(rows[i + 1]); } }
  else if (e.key === "ArrowLeft") { e.preventDefault(); if (isOpen(li)) collapse(li); else focusRow(li.parentElement.closest(".pt-node")?.querySelector(":scope > .pt-row")); }
  else if (e.key === "Enter" || e.key === " ") { e.preventDefault(); row.click(); }
});
el.tree.addEventListener("focusin", (e) => { const row = e.target.closest(".pt-row"); if (row) { el.tree.querySelectorAll(".pt-row[tabindex='0']").forEach((r) => { if (r !== row) r.tabIndex = -1; }); row.tabIndex = 0; } });

el.collapse.addEventListener("click", () => {
  el.tree.querySelectorAll(".pt-node[aria-expanded='true']").forEach((li) => { li.setAttribute("aria-expanded", "false"); kidsOf(li).hidden = true; });
  remember([]);
});

el.chips.addEventListener("click", (e) => {
  const b = e.target.closest("[data-mode]");
  if (!b) return;
  indexedOnly = b.dataset.mode === "indexed";
  updateUrl({ v: "ptree", indexed: indexedOnly });
  remember([]);
  start();
});

/* ───────── 搜索与定位 ───────── */
el.form.addEventListener("submit", async (e) => {
  e.preventDefault();
  const q = el.q.value.trim();
  if (!q) { el.results.hidden = true; el.results.innerHTML = ""; return; }
  el.results.hidden = false;
  el.results.innerHTML = '<p class="sheet-note"><span class="spinner"></span>查找中…</p>';
  try {
    const r = await api("/api/portal-tree/search", { q });
    results = r.results;
    el.results.innerHTML = r.results.length
      ? `<ul class="plain">${r.results.map((x, i) => `<li><div class="pt-hit-name"><strong>${esc(x.node.name)}</strong><span class="badge${LEVEL_CLS[x.node.level] || ""}">${esc(x.node.level_zh)}</span></div><small>${esc(x.path)}</small>
        <div class="row-foot"><button type="button" class="tnode-go" data-locate="${i}">在树中定位</button><button type="button" class="tnode-go" data-detail="${i}">详情</button>${x.node.home_url ? extLink(x.node.home_url, "官网", "tnode-go") : ""}</div></li>`).join("")}</ul><button type="button" class="link-btn" data-close="1">收起搜索结果</button>`
      : '<p class="sheet-note">没有找到匹配的门户，可以换个说法。</p>';
  } catch (err) { el.results.innerHTML = `<p class="sheet-note">${esc(err.message)}</p>`; }
});

el.results.addEventListener("click", async (e) => {
  if (e.target.closest("[data-close]")) { el.results.hidden = true; return; }
  const d = e.target.closest("[data-detail]");
  if (d) return openSourceDetail(results[Number(d.dataset.detail)].node.portal_id);
  const b = e.target.closest("[data-locate]");
  if (!b) return;
  const hit = results[Number(b.dataset.locate)];
  const trail = hit.trail;
  for (let i = 0; i < trail.length; i++) {
    const li = el.tree.querySelector(`.pt-node[data-id="${CSS.escape(trail[i])}"]`);
    if (!li) return;
    const last = i === trail.length - 1;
    const needFocus = last && !el.tree.querySelector(`.pt-node[data-id="${CSS.escape(hit.node.id)}"]`);
    await expand(li, needFocus ? { focus: hit.node.portal_id } : {});
  }
  const target = el.tree.querySelector(`.pt-node[data-id="${CSS.escape(hit.node.id)}"]`);
  if (target) {
    el.tree.querySelectorAll(".pt-hit").forEach((x) => x.classList.remove("pt-hit"));
    const row = target.querySelector(":scope > .pt-row");
    row.classList.add("pt-hit");
    target.scrollIntoView({ block: "center", behavior: "smooth" });
    focusRow(row);
  }
});

/* ───────── 入口 ───────── */
async function restore() {
  for (const id of saved()) {
    const li = el.tree.querySelector(`.pt-node[data-id="${CSS.escape(id)}"]`);
    if (li && !isOpen(li)) await expand(li);
  }
}

async function start() {
  el.chips.innerHTML = [["all", "全部门户"], ["indexed", "只看已接入（可点到资源）"]].map(([m, t]) => `<button type="button" class="chip${(m === "indexed") === indexedOnly ? " on" : ""}" data-mode="${m}">${t}</button>`).join("");
  nodes.clear();
  el.tree.innerHTML = '<ul class="pt-kids pt-root" role="tree" aria-label="数据门户"></ul>';
  const ok = await load(null);
  if (ok) { await restore(); focusRow(el.tree.querySelector(".pt-row")); }
}

export function enter() {
  document.title = "门户树 · DataFinder";
  indexedOnly = currentParams().indexed === "1";
  start();
}
