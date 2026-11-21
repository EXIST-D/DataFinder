"use strict";
/* 目录树：同一批资源按 主题 / 地域 / 来源 逐级展开。无状态，位置与筛选全部写进 URL；节点只负责导航，选中后回到资源目录看列表。 */

import { $, api, emptyPanel, esc, ICON_CHEVRON, skeleton } from "./util.js";
import { currentParams, go, updateUrl } from "./router.js";
import { openDetail } from "./catalog.js";

const el = { form: $("tForm"), q: $("tQ"), axes: $("tAxes"), crumbs: $("tCrumbs"), list: $("tList"), note: $("tNote") };
const AXES = [["topic", "按主题"], ["region", "按地域"], ["source", "按来源"]];
const CARRY = ["topic", "region", "kind", "delivery", "domain", "source"]; // 从资源目录带过来的筛选
const PAGE = 100;
let state = { axis: "topic", node: "", q: "", strict: false, carry: {}, offset: 0 };
let seq = 0;
let shown = [];

const fromParams = (p) => ({
  axis: AXES.some(([a]) => a === p.axis) ? p.axis : "topic",
  node: p.node || "", q: p.q || "", strict: p.strict === "1", offset: 0,
  carry: Object.fromEntries(CARRY.filter((k) => p[k]).map((k) => [k, p[k]])),
});

const syncUrl = () => updateUrl({ v: "tree", axis: state.axis, node: state.node, q: state.q, strict: state.strict, ...Object.fromEntries(CARRY.map((k) => [k, state.carry[k] || ""])) });

function change(patch) {
  state = { ...state, ...patch, offset: patch.offset ?? 0 };
  syncUrl();
  load();
}

function renderAxes() {
  el.axes.innerHTML = AXES.map(([a, name]) => `<button type="button" class="chip${state.axis === a ? " on" : ""}" role="tab" aria-selected="${state.axis === a}" data-axis="${a}">${name}</button>`).join("");
}

function renderCrumbs(path) {
  const items = [`<button type="button" class="crumb${path.length ? "" : " cur"}" data-node="">顶层</button>`];
  path.forEach((p, i) => {
    items.push('<span class="crumb-sep" aria-hidden="true">›</span>');
    items.push(`<button type="button" class="crumb${i === path.length - 1 ? " cur" : ""}" data-node="${esc(p.id)}">${esc(p.name)}</button>`);
  });
  el.crumbs.innerHTML = items.join("");
}

/** 把节点的 select（与检索参数同名）合并进当前筛选，去资源目录看这批资源。 */
function openList(node) {
  const params = { q: state.q, strict: state.strict, ...state.carry, ...node.select };
  go("catalog", params);
}

function nodeRow(n, i) {
  const open = n.has_children;
  const goBtn = n.select && !n.select.inspect && !n.leaf ? `<button type="button" class="tnode-go" data-go="${i}">看资源</button>` : "";
  const cls = `tnode${open || n.leaf ? " clickable" : ""}${n.select === null ? " muted" : ""}`;
  const kind = n.kind ? `<small>${esc(n.kind)}</small>` : n.level ? `<small>${esc(n.level)}</small>` : "";
  return `<li class="${cls}" data-i="${i}"${open || n.leaf ? ' tabindex="0" role="button"' : ""}>
    <span class="tnode-name">${esc(n.name)}${kind}</span>${goBtn}<span class="tnode-n">${n.count.toLocaleString("zh-CN")}</span>${open ? ICON_CHEVRON : ""}</li>`;
}

async function load() {
  const mine = ++seq;
  el.q.value = state.q;
  renderAxes();
  el.list.innerHTML = skeleton(4);
  try {
    const res = await api("/api/catalog/tree", {
      axis: state.axis, node: state.node, q: state.q, mode: state.strict ? "strict" : "discovery",
      ...state.carry, limit: PAGE, offset: state.offset,
    });
    if (mine !== seq) return;
    shown = state.offset ? [...shown, ...res.children] : res.children;
    renderCrumbs(res.path);
    el.note.textContent = [`当前条件下共 ${res.total.toLocaleString("zh-CN")} 条资源。`, res.note, res.unannotated ? `${res.unannotated.toLocaleString("zh-CN")} 条没有地域标注。` : "", ...res.hints].filter(Boolean).join(" ");
    const more = res.total_children > shown.length ? `<button type="button" class="tree-more" data-more="1">再显示 ${Math.min(PAGE, res.total_children - shown.length)} 项（共 ${res.total_children}）</button>` : "";
    el.list.innerHTML = shown.length
      ? `<ul class="plain">${shown.map(nodeRow).join("")}</ul>${more}`
      : `<div class="empty"><h3>这里没有更多节点</h3><p>当前条件下没有命中的资源，可以放宽关键词或回到顶层。</p></div>`;
  } catch (e) {
    if (mine !== seq) return;
    el.list.innerHTML = emptyPanel(e.status === 503 ? "资源目录还没建立" : "加载失败", e.message);
  }
}

el.form.addEventListener("submit", (e) => { e.preventDefault(); change({ q: el.q.value.trim(), node: "" }); });
el.axes.addEventListener("click", (e) => { const b = e.target.closest("[data-axis]"); if (b && b.dataset.axis !== state.axis) { shown = []; change({ axis: b.dataset.axis, node: "" }); } });
el.crumbs.addEventListener("click", (e) => { const b = e.target.closest("[data-node]"); if (b) { shown = []; change({ node: b.dataset.node }); } });
el.list.addEventListener("click", (e) => {
  if (e.target.closest("[data-more]")) { change({ offset: shown.length }); return; }
  const g = e.target.closest("[data-go]");
  if (g) { openList(shown[Number(g.dataset.go)]); return; }
  const r = e.target.closest("[data-i]");
  if (!r) return;
  const n = shown[Number(r.dataset.i)];
  if (n.has_children) { shown = []; change({ node: n.id }); }
  else if (n.leaf && n.select?.inspect) openDetail(n.select.inspect);
});
el.list.addEventListener("keydown", (e) => { if (e.key === "Enter") e.target.closest("[data-i]")?.click(); });

export function enter() {
  document.title = "目录树 · DataFinder";
  shown = [];
  state = fromParams(currentParams());
  load();
}
