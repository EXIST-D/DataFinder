"use strict";
/* 门户树：左树 · 中列表 · 右详情。
   左：国家 → 地区 → 机构类型 → 门户 → 来源 → 资源，逐层展开（按需加载，可键盘操作）；也可换成按数据形态/机构类型分。
   中：选中节点之下的全部门户（拍平、排序、分页）。右：概况（分布、可点击筛选）或门户/来源/资源详情。
   筛选（数据形态、只看官方、有 API、只看已接入）同时作用于树的计数、列表与概况；状态写进网址，可分享。 */

import { $, api, esc, extLink } from "./util.js";
import { currentParams, go, updateUrl } from "./router.js";
import { openDetail as openPortalDetail } from "./sources.js";
import { openDetail as openResourceDetail } from "./catalog.js";

const el = {
  form: $("ptForm"), q: $("ptQ"), axes: $("ptAxes"), filters: $("ptFilters"), active: $("ptActive"), results: $("ptResults"),
  tree: $("ptTree"), collapse: $("ptCollapse"), crumbs: $("ptCrumbs"), midHead: $("ptMidHead"), list: $("ptList"), detail: $("ptDetail"), note: $("ptNote"),
};
const PAGE = 100;
const LIST_PAGE = 30;
const STORE = "datafinder.ptree.open";
const AXES = [["geo", "按地域"], ["kind", "按数据形态"], ["tier", "按机构类型"]];
const LEVEL_CLS = { indexed_and_fetchable: " dl-generate", indexed: "", directory_only: " dl-link" };
const CAP = { fetch: "可实时取数", download: "有文件直链", discover: "可检索" };
const SCOPE_KINDS = new Set(["country", "region", "tier", "group"]);
const EMPTY_F = { kind: "", official: false, api: false, indexed: false };
const fmt = (n) => Number(n).toLocaleString("zh-CN");
const svg = (d, extra = "") => `<svg class="pt-ico" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${d}${extra}</svg>`;
const ICON = {
  folder: svg('<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>'),
  portal: svg('<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c3 3.5 3 14.5 0 18M12 3c-3 3.5-3 14.5 0 18"/>'),
  source: svg('<ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v6c0 1.7 3.6 3 8 3s8-1.3 8-3V5M4 11v6c0 1.7 3.6 3 8 3s8-1.3 8-3v-6"/>'),
  doc: svg('<path d="M6 3h8l4 4v14H6z"/><path d="M14 3v4h4"/>'),
};
const CARET = '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M9 6l6 6-6 6"/></svg>';
const iconOf = (n) => (n.kind === "portal" ? ICON.portal : n.kind === "source" ? ICON.source : n.kind === "resource" ? ICON.doc : ICON.folder);

// sel：当前选中的节点（右边详情）；mid：中间列表对应的节点（门户被选中时，中间仍显示它所在的那组）
const S = { sel: "", mid: "", axis: "geo", f: { ...EMPTY_F }, sort: "official", kinds: [] };
const nodes = new Map(); // id -> 节点数据 + 已加载数量 + 深度
const seq = { tree: 0, mid: 0, right: 0 };
let results = [];
let midItems = [];
let midLoaded = 0;
let midTotal = 0;

const fp = () => ({ kind: S.f.kind, official: S.f.official, api: S.f.api, indexed: S.f.indexed });
const isScope = (id) => !id || /^[crts]:/.test(id);
const saved = () => { try { return JSON.parse(sessionStorage.getItem(STORE) || "[]"); } catch { return []; } };
const remember = (ids) => { try { sessionStorage.setItem(STORE, JSON.stringify(ids.slice(-400))); } catch { /* 无痕模式 */ } };
const setOpen = (id, on) => { const cur = saved().filter((x) => x !== id); remember(on ? [...cur, id] : cur); };
const q1 = (id) => el.tree.querySelector(`.pt-node[data-id="${CSS.escape(id)}"]`);
const urlState = () => updateUrl({ v: "ptree", sel: S.sel, axis: S.axis === "geo" ? "" : S.axis, kind: S.f.kind, official: S.f.official, api: S.f.api, indexed: S.f.indexed });

/* ───────── 左：树 ───────── */
function nodeHtml(n, depth, pos, size) {
  nodes.set(n.id, { ...n, loaded: 0, total: 0, depth });
  const count = n.count != null ? n.count : n.kind === "portal" ? n.indexed_resources : 0;
  const cnt = count ? `<span class="pt-n">${fmt(count)}</span>` : "";
  const dot = n.kind === "portal" ? `<span class="pt-dot ${esc(n.level)}" title="${esc(n.level_zh)}"></span>` : "";
  const caret = n.has_children ? `<button type="button" class="pt-caret" tabindex="-1" aria-label="展开或收起">${CARET}</button>` : '<span class="pt-caret none"></span>';
  return `<li class="pt-node" role="treeitem" data-id="${esc(n.id)}" data-kind="${esc(n.kind)}" aria-level="${depth}" aria-posinset="${pos}" aria-setsize="${size}" aria-selected="false"${n.has_children ? ' aria-expanded="false"' : ""}>
    <div class="pt-row" tabindex="-1" title="${esc(n.name)}">${caret}${iconOf(n)}${dot}<span class="pt-name">${esc(n.name)}</span>${cnt}</div>
    ${n.has_children ? '<ul class="pt-kids" role="group" hidden></ul>' : ""}</li>`;
}

const kidsOf = (li) => li.querySelector(":scope > .pt-kids");
const isOpen = (li) => li.getAttribute("aria-expanded") === "true";

async function loadKids(li, { more = false, focus = "" } = {}) {
  const id = li?.dataset.id ?? "";
  const st = id ? nodes.get(id) : { loaded: 0, depth: 0 };
  const kids = li ? kidsOf(li) : el.tree.querySelector(":scope > .pt-kids");
  if (!more) { kids.innerHTML = '<li class="pt-msg"><span class="spinner"></span>读取中…</li>'; st.loaded = 0; }
  else kids.querySelector(".pt-more-li")?.remove();
  try {
    const r = await api("/api/portal-tree", { node: id, limit: id ? PAGE : 300, offset: more ? st.loaded : 0, focus, axis: S.axis, ...fp() });
    if (!more) kids.innerHTML = "";
    const base = more ? st.loaded : r.offset;
    kids.insertAdjacentHTML("beforeend", r.children.map((c, i) => nodeHtml(c, st.depth + 1, base + i + 1, r.total_children)).join(""));
    st.loaded = base + r.children.length;
    st.total = r.total_children;
    if (st.loaded < st.total) kids.insertAdjacentHTML("beforeend", `<li class="pt-more-li"><button type="button" class="tree-more" data-more="${esc(id)}">再显示 ${Math.min(PAGE, st.total - st.loaded)} 项（共 ${fmt(st.total)}）</button></li>`);
    if (!r.children.length && !more) kids.innerHTML = '<li class="pt-msg">没有符合当前筛选的节点</li>';
    if (!id) el.note.textContent = `共 ${fmt(r.total_children)} 个${S.axis === "geo" ? "国家/地区" : "分组"}。选中任意一层，中间看这一层的全部门户；带绿点或蓝点的是已接入的门户，可以继续点开到资源。`;
    markSel();
    return true;
  } catch (e) {
    kids.innerHTML = `<li class="pt-msg warn">${esc(e.status === 503 ? "门户库还没建立：先运行 datafinder sync" : e.message)}</li>`;
    return false;
  }
}

async function expand(li, opts = {}) {
  if (!li || !li.hasAttribute("aria-expanded")) return;
  const st = nodes.get(li.dataset.id);
  li.setAttribute("aria-expanded", "true");
  kidsOf(li).hidden = false;
  if (!st.loaded || opts.focus) await loadKids(li, opts);
  setOpen(li.dataset.id, true);
}
function collapse(li) {
  if (!li || !li.hasAttribute("aria-expanded")) return;
  li.setAttribute("aria-expanded", "false");
  kidsOf(li).hidden = true;
  setOpen(li.dataset.id, false);
}
const toggle = (li) => (isOpen(li) ? collapse(li) : expand(li));

function markSel() {
  el.tree.querySelectorAll(".pt-node.sel").forEach((x) => { x.classList.remove("sel"); x.setAttribute("aria-selected", "false"); });
  const li = S.sel ? q1(S.sel) : null;
  if (li) { li.classList.add("sel"); li.setAttribute("aria-selected", "true"); }
  el.list.querySelectorAll(".pt-card.sel").forEach((x) => x.classList.remove("sel"));
  if (S.sel) el.list.querySelector(`.pt-card[data-id="${CSS.escape(S.sel)}"]`)?.classList.add("sel");
}

/* ───────── 选择：右边概况/详情，中间列表跟着变 ───────── */
async function select(id, { reveal = false, force = false } = {}) {
  S.sel = id;
  urlState();
  markSel();
  if (reveal) { const li = q1(id); if (li) { li.scrollIntoView({ block: "nearest" }); focusRow(li.querySelector(":scope > .pt-row")); } }
  const mine = ++seq.right;
  el.detail.innerHTML = '<div class="pt-d"><span class="spinner"></span> 读取中…</div>';
  let s;
  try { s = await api("/api/portal-tree/summary", { node: id, ...fp() }); }
  catch (e) { if (mine === seq.right) el.detail.innerHTML = `<div class="pt-d"><p>${esc(e.message)}</p></div>`; return; }
  if (mine !== seq.right) return;
  // 门户被选中时，中间仍是它所在的那一组；目录节点（来源/资源）中间列它的下级
  const mid = s.kind === "p" ? (s.path.length > 1 ? s.path[s.path.length - 2].id : "") : id;
  if (force || mid !== S.mid || !midItems.length) { S.mid = mid; loadMid(true); } else markSel();
  renderCrumbs(s.path);
  if (s.kind !== "scope" && !S.kinds.length) api("/api/portal-tree/summary", { node: isScope(mid) ? mid : "", ...fp() }).then((m) => { if (m.kinds) renderFilters(m.kinds); }).catch(() => {});
  if (s.kind === "scope") renderScope(s);
  else if (s.kind === "p") renderPortal(s, mine);
  else renderCatalog(s, mine);
}

function renderCrumbs(path) {
  const items = [`<button type="button" class="crumb${path.length ? "" : " cur"}" data-sel="">全部门户</button>`];
  path.forEach((p, i) => {
    items.push('<span class="crumb-sep" aria-hidden="true">›</span>');
    items.push(`<button type="button" class="crumb${i === path.length - 1 ? " cur" : ""}" data-sel="${esc(p.id)}">${esc(p.name)}</button>`);
  });
  el.crumbs.innerHTML = items.join("");
}

/* ───────── 中：列表 ───────── */
function cardHtml(n, i) {
  if (n.kind === "source") {
    return `<li class="pt-card" data-i="${i}" data-id="${esc(n.id)}"><div class="pt-card-top"><span class="pt-card-name">${esc(n.name)}</span><span class="badge">目录来源</span>${n.capabilities?.includes("fetch") ? '<span class="badge dl-generate">可取数</span>' : ""}</div><div class="pt-card-meta">门户库里没有对应记录 · ${fmt(n.count)} 条资源</div></li>`;
  }
  const meta = [[n.country, n.region].filter(Boolean).join(" › "), n.org_type, n.data_kind, n.openness].filter(Boolean).join(" · ");
  return `<li class="pt-card${S.sel === n.id ? " sel" : ""}" data-i="${i}" data-id="${esc(n.id)}">
    <div class="pt-card-top"><span class="pt-card-name">${esc(n.name)}</span><span class="badge${LEVEL_CLS[n.level] || ""}">${esc(n.level_zh)}</span>${n.official ? '<span class="pill">官方</span>' : ""}${n.api ? '<span class="pill">API</span>' : ""}</div>
    <div class="pt-card-meta">${esc(meta)}</div>${n.description ? `<p class="pt-card-desc">${esc(n.description)}</p>` : ""}
    <div class="pt-card-foot">${n.indexed_resources ? `<span class="pill res-pill">${fmt(n.indexed_resources)} 条资源</span>` : ""}${n.home_url ? extLink(n.home_url, "官网", "tnode-go") : ""}<button type="button" class="tnode-go" data-act="open">详情</button></div></li>`;
}

function childRow(n, i) {
  const c = n.count != null ? `<span class="pt-n">${fmt(n.count)}</span>` : "";
  return `<li class="pt-card${S.sel === n.id ? " sel" : ""}" data-i="${i}" data-id="${esc(n.id)}"><div class="pt-card-top">${iconOf(n)}<span class="pt-card-name">${esc(n.name)}</span>${n.resource_type ? `<span class="pill">${esc(n.resource_type)}</span>` : ""}${c}</div></li>`;
}

async function loadMid(reset) {
  const mine = ++seq.mid;
  if (reset) { midItems = []; midLoaded = 0; el.list.scrollTop = 0; el.list.innerHTML = '<p class="pt-empty"><span class="spinner"></span> 读取中…</p>'; }
  const scope = isScope(S.mid);
  try {
    const r = scope
      ? await api("/api/portal-tree/list", { node: S.mid, limit: LIST_PAGE, offset: midLoaded, sort: S.sort, ...fp() })
      : await api("/api/portal-tree", { node: S.mid, limit: LIST_PAGE, offset: midLoaded, axis: S.axis, ...fp() });
    if (mine !== seq.mid) return;
    const got = scope ? r.items : r.children;
    midItems = [...midItems, ...got];
    midLoaded += got.length;
    midTotal = scope ? r.total : r.total_children;
    el.midHead.innerHTML = `<span><strong>${fmt(midTotal)}</strong> ${scope ? "个门户" : "项"}</span>${scope ? `<select class="pt-mid-sort" id="ptSort" aria-label="排序"><option value="official"${S.sort === "official" ? " selected" : ""}>官方优先</option><option value="name"${S.sort === "name" ? " selected" : ""}>按名称</option></select>` : ""}`;
    const rows = midItems.map((n, i) => (scope ? cardHtml(n, i) : childRow(n, i))).join("");
    const more = midLoaded < midTotal ? `<li class="pt-more-li"><button type="button" class="tree-more" data-midmore="1">再显示 ${Math.min(LIST_PAGE, midTotal - midLoaded)} 项（共 ${fmt(midTotal)}）</button></li>` : "";
    el.list.innerHTML = midItems.length ? `<ul class="pt-cards">${rows}${more}</ul>` : '<p class="pt-empty">没有符合当前筛选的内容。可以去掉一些筛选条件。</p>';
  } catch (e) {
    if (mine === seq.mid) el.list.innerHTML = `<p class="pt-empty">${esc(e.status === 503 ? "门户库还没建立：先运行 datafinder sync" : e.message)}</p>`;
  }
}

/* ───────── 右：概况与详情 ───────── */
const bar = (name, n, max, attr) => `<button type="button" ${attr}>${esc(name)}</button><meter min="0" max="${max}" value="${n}"></meter><span class="n">${fmt(n)}</span>`;

function renderFilters(kinds) {
  S.kinds = kinds;
  const on = (v) => (v ? " on" : "");
  const kindChips = kinds.map((k) => `<button type="button" class="chip${on(S.f.kind === k.key)}" data-kind="${esc(k.key)}">${esc(k.name)} <small>${fmt(k.count)}</small></button>`).join("");
  el.filters.innerHTML = `${kindChips}${kinds.length ? '<span class="sep"></span>' : ""}
    <button type="button" class="chip${on(S.f.official)}" data-tog="official">只看官方</button>
    <button type="button" class="chip${on(S.f.api)}" data-tog="api">有 API</button>
    <button type="button" class="chip${on(S.f.indexed)}" data-tog="indexed">只看已接入</button>`;
  const act = [];
  if (S.f.kind) act.push(["kind", `数据形态：${kinds.find((k) => k.key === S.f.kind)?.name || S.f.kind}`]);
  if (S.f.official) act.push(["official", "只看官方"]);
  if (S.f.api) act.push(["api", "有 API"]);
  if (S.f.indexed) act.push(["indexed", "只看已接入"]);
  el.active.innerHTML = act.map(([k, t]) => `<button type="button" class="achip" data-clear="${k}">${esc(t)} <span aria-hidden="true">×</span><span class="sr">移除</span></button>`).join("") + (act.length > 1 ? '<button type="button" class="achip clear" data-clear="*">清除全部</button>' : "");
}

function renderScope(s) {
  renderFilters(s.kinds);
  const maxK = Math.max(1, ...s.kinds.map((k) => k.count));
  const maxT = Math.max(1, ...s.tiers.map((t) => t.count));
  el.detail.innerHTML = `<div class="pt-d"><h2>${esc(s.name)}</h2>
    <p class="sub">${fmt(s.total)} 个门户${s.total !== s.portals ? `（含 ${fmt(s.total - s.portals)} 个仅目录里有的来源）` : ""}</p>
    <div class="pt-stats">
      <button type="button" class="pt-stat" data-tog="official"><b>${fmt(s.official)}</b><span>官方机构</span></button>
      <button type="button" class="pt-stat" data-tog="api"><b>${fmt(s.api)}</b><span>有 API</span></button>
      <button type="button" class="pt-stat" data-tog="indexed"><b>${fmt(s.indexed)}</b><span>已接入</span></button></div>
    <h3>按数据形态（点击筛选）</h3><div class="pt-bars">${s.kinds.map((k) => bar(k.name, k.count, maxK, `data-kind="${esc(k.key)}"`)).join("") || '<span class="sub">没有数据</span>'}</div>
    <h3>按机构类型</h3><div class="pt-bars">${s.tiers.map((t) => `<span>${esc(t.name)}</span><meter min="0" max="${maxT}" value="${t.count}"></meter><span class="n">${fmt(t.count)}</span>`).join("")}</div>
    ${s.indexed ? '<h3>已接入的门户</h3><ul class="pt-list-mini" id="ptIdx"><li><span class="spinner"></span></li></ul>' : ""}</div>`;
  if (s.indexed) loadIndexed(s.node || "");
}

async function loadIndexed(node) {
  const mine = seq.right;
  try {
    const r = await api("/api/portal-tree/list", { node, limit: 6, ...fp(), indexed: true });
    if (mine !== seq.right) return;
    const box = $("ptIdx");
    if (box) box.innerHTML = r.items.map((n) => `<li data-id="${esc(n.id)}"><span>${esc(n.name)}</span><small>${n.indexed_resources ? `${fmt(n.indexed_resources)} 条` : esc(n.level_zh || "")}</small></li>`).join("");
  } catch { /* 只是示例 */ }
}

async function renderPortal(s, mine) {
  const n = s.portal;
  renderFilters(S.kinds);
  el.detail.innerHTML = `<div class="pt-d"><h2>${esc(n.name)}</h2><p class="sub">${esc([n.country, n.region].filter(Boolean).join(" › "))}</p>
    <div class="tags"><span class="badge${LEVEL_CLS[n.level] || ""}">${esc(n.level_zh)}</span>${n.official ? '<span class="pill">官方</span>' : ""}${n.api ? '<span class="pill">API</span>' : ""}${n.verify === "blocked" ? '<span class="pill">拒绝自动访问</span>' : ""}</div>
    ${n.description ? `<p>${esc(n.description)}</p>` : ""}
    <dl class="pt-kv"><dt>官网</dt><dd>${n.home_url ? extLink(n.home_url, n.home_url, "link-btn") : "—"}</dd><dt>机构类型</dt><dd>${esc(n.org_type || "—")}</dd><dt>数据形态</dt><dd>${esc(n.data_kind || "—")}</dd><dt>开放程度</dt><dd>${esc(n.openness || "—")}</dd>${n.industries?.length ? `<dt>领域</dt><dd>${esc(n.industries.join("、"))}</dd>` : ""}${n.note ? `<dt>备注</dt><dd>${esc(n.note)}</dd>` : ""}</dl>
    <div id="ptPortalMore"><p class="sub"><span class="spinner"></span> 读取接入情况…</p></div></div>`;
  try {
    const d = await api("/api/data-sources/detail", { id: n.portal_id });
    if (mine !== seq.right) return;
    const roots = d.sources.map((src) => `<h3>${esc(src.name)}（${fmt(src.resources)} 条资源）</h3><div class="pt-actions-row">${src.roots.map((r) => `<button type="button" class="pt-btn" data-sel="k:${esc(r.id)}">${esc(r.name)} ${fmt(r.count)}</button>`).join("")}</div>`).join("");
    $("ptPortalMore").innerHTML = `<h3>在 DataFinder 里的接入情况</h3><dl class="pt-kv"><dt>等级</dt><dd>${esc(d.integration.level_zh)}</dd><dt>已索引资源</dt><dd>${fmt(d.integration.indexed_resources)} 条<br><small>来自本机目录，不等于原站全部数据</small></dd>${d.integration.capabilities.length ? `<dt>能力</dt><dd>${esc(d.integration.capabilities.map((c) => CAP[c] || c).join("、"))}</dd>` : ""}</dl>
      ${d.usage.length ? `<h3>使用说明</h3>${d.usage.map((u) => `<p>${esc(u)}</p>`).join("")}` : ""}${roots}
      <div class="pt-actions-row">${d.integration.linked_source_ids.length ? `<button type="button" class="pt-btn solid" data-catalog="${esc(d.integration.linked_source_ids.join(","))}">在资源目录里看资源 ›</button>` : ""}<button type="button" class="pt-btn" data-full="${esc(n.portal_id)}">完整详情</button></div>`;
  } catch (e) {
    if (mine === seq.right) $("ptPortalMore").innerHTML = `<p class="sub">${esc(e.message)}</p>`;
  }
}

async function renderCatalog(s, mine) {
  const id = S.sel.slice(2);
  const name = s.path.at(-1)?.name || "";
  el.detail.innerHTML = `<div class="pt-d"><h2>${esc(name)}</h2><p class="sub">${esc(s.path.map((p) => p.name).join(" › "))}</p><div id="ptCatMore"><span class="spinner"></span></div></div>`;
  try {
    if (id.startsWith("source:")) {
      const sid = id.slice(7);
      const d = await api("/api/data-sources/detail", { id: `source:${sid}` });
      if (mine !== seq.right) return;
      const src = d.sources[0];
      $("ptCatMore").innerHTML = `<div class="tags"><span class="badge${LEVEL_CLS[d.integration.level] || ""}">${esc(d.integration.level_zh)}</span></div>
        <dl class="pt-kv"><dt>资源数</dt><dd>${fmt(src?.resources || 0)} 条</dd><dt>能力</dt><dd>${esc((src?.capabilities || []).map((c) => CAP[c] || c).join("、") || "—")}</dd>${d.home_url ? `<dt>官网</dt><dd>${extLink(d.home_url, d.home_url, "link-btn")}</dd>` : ""}</dl>
        ${d.usage.map((u) => `<p>${esc(u)}</p>`).join("")}
        <div class="pt-actions-row"><button type="button" class="pt-btn solid" data-catalog="${esc(sid)}">在资源目录里看 ›</button><button type="button" class="pt-btn" data-tree="${esc(id)}">在目录树中展开</button></div>`;
      return;
    }
    const rid = id.startsWith("res:") ? id.slice(4) : id;
    const d = await api("/api/catalog/inspect", { id: rid, children: false });
    if (mine !== seq.right) return;
    const r = d.resource;
    $("ptCatMore").innerHTML = `<div class="tags"><span class="pill">${esc(r.kind)}</span><span class="badge">${esc({ download: "可下载", generate: "可取数", link: "仅链接" }[r.delivery] || r.delivery)}</span></div>
      <dl class="pt-kv">${r.unit ? `<dt>单位</dt><dd>${esc(r.unit)}</dd>` : ""}<dt>频度</dt><dd>${esc(r.frequency || "未知")}</dd><dt>获取</dt><dd>${esc(d.how_to_get?.note || "")}</dd></dl>
      ${d.glossary?.length ? `<h3>术语说明</h3>${d.glossary.map((t) => `<p><strong>${esc(t.name_zh)}</strong>（${esc(t.status_zh)}）${esc(t.definition)}</p>`).join("")}` : ""}
      <div class="pt-actions-row"><button type="button" class="pt-btn solid" data-res="${esc(rid)}">完整详情</button>${d.child_count ? '<span class="sub">中间列表是它的下级</span>' : ""}</div>`;
  } catch (e) {
    if (mine === seq.right) $("ptCatMore").innerHTML = `<p class="sub">${esc(e.message)}</p>`;
  }
}

/* ───────── 交互：树与键盘 ───────── */
function focusRow(row) {
  if (!row) return;
  el.tree.querySelectorAll(".pt-row[tabindex='0']").forEach((r) => { r.tabIndex = -1; });
  row.tabIndex = 0;
  row.focus();
}
const visibleRows = () => [...el.tree.querySelectorAll(".pt-row")].filter((r) => !r.closest(".pt-kids[hidden]"));

el.tree.addEventListener("click", (e) => {
  const more = e.target.closest("[data-more]");
  if (more) { loadKids(more.dataset.more ? q1(more.dataset.more) : null, { more: true }); return; }
  const li = e.target.closest(".pt-node");
  if (!li) return;
  if (e.target.closest(".pt-caret") && li.hasAttribute("aria-expanded")) { toggle(li); return; }
  const n = nodes.get(li.dataset.id);
  select(li.dataset.id);
  if (li.hasAttribute("aria-expanded") && !isOpen(li) && (SCOPE_KINDS.has(n?.kind) || n?.kind === "portal")) expand(li);
});

let typed = "";
let typedAt = 0;
el.tree.addEventListener("keydown", (e) => {
  const row = e.target.closest(".pt-row");
  if (!row) return;
  const li = row.closest(".pt-node");
  const rows = visibleRows();
  const i = rows.indexOf(row);
  const k = e.key;
  if (k === "ArrowDown") { e.preventDefault(); focusRow(rows[i + 1]); }
  else if (k === "ArrowUp") { e.preventDefault(); focusRow(rows[i - 1]); }
  else if (k === "Home") { e.preventDefault(); focusRow(rows[0]); }
  else if (k === "End") { e.preventDefault(); focusRow(rows.at(-1)); }
  else if (k === "ArrowRight") { e.preventDefault(); if (li.hasAttribute("aria-expanded")) { if (!isOpen(li)) expand(li); else focusRow(rows[i + 1]); } }
  else if (k === "ArrowLeft") { e.preventDefault(); if (isOpen(li)) collapse(li); else focusRow(li.parentElement.closest(".pt-node")?.querySelector(":scope > .pt-row")); }
  else if (k === "Enter") { e.preventDefault(); select(li.dataset.id); }
  else if (k === " ") { e.preventDefault(); toggle(li); }
  else if (k.length === 1 && !e.ctrlKey && !e.metaKey && !e.altKey) { // 输入首字母快速定位
    const now = Date.now();
    typed = now - typedAt > 700 ? k.toLowerCase() : typed + k.toLowerCase();
    typedAt = now;
    const order = [...rows.slice(i + (typed.length === 1 ? 1 : 0)), ...rows.slice(0, i + 1)];
    focusRow(order.find((r) => r.querySelector(".pt-name").textContent.toLowerCase().startsWith(typed)));
  }
});
el.tree.addEventListener("focusin", (e) => { const row = e.target.closest(".pt-row"); if (row) { el.tree.querySelectorAll(".pt-row[tabindex='0']").forEach((r) => { if (r !== row) r.tabIndex = -1; }); row.tabIndex = 0; } });
el.collapse.addEventListener("click", () => {
  el.tree.querySelectorAll(".pt-node[aria-expanded='true']").forEach((li) => { li.setAttribute("aria-expanded", "false"); kidsOf(li).hidden = true; });
  remember([]);
});

/* ───────── 交互：列表、右栏、筛选、轴 ───────── */
el.list.addEventListener("click", (e) => {
  if (e.target.closest("a")) return;
  if (e.target.closest("[data-midmore]")) { loadMid(false); return; }
  const card = e.target.closest(".pt-card");
  if (!card) return;
  const n = midItems.find((x) => x.id === card.dataset.id);
  if (e.target.closest("[data-act='open']")) { if (n?.portal_id) openPortalDetail(n.portal_id); return; }
  select(card.dataset.id);
  const li = q1(card.dataset.id);
  if (li) li.scrollIntoView({ block: "nearest" });
});
el.midHead.addEventListener("change", (e) => { if (e.target.id === "ptSort") { S.sort = e.target.value; loadMid(true); } });

async function applyFilters() {
  urlState();
  await startTree();
  select(S.sel, { force: true });
}

function onFilterClick(e) {
  const k = e.target.closest("[data-kind]");
  const t = e.target.closest("[data-tog]");
  const c = e.target.closest("[data-clear]");
  if (k) { S.f.kind = S.f.kind === k.dataset.kind ? "" : k.dataset.kind; applyFilters(); }
  else if (t) { S.f[t.dataset.tog] = !S.f[t.dataset.tog]; applyFilters(); }
  else if (c) { if (c.dataset.clear === "*") S.f = { ...EMPTY_F }; else S.f[c.dataset.clear] = c.dataset.clear === "kind" ? "" : false; applyFilters(); }
}
el.filters.addEventListener("click", onFilterClick);
el.active.addEventListener("click", onFilterClick);

el.detail.addEventListener("click", (e) => {
  if (e.target.closest("a")) return;
  if (e.target.closest("[data-kind],[data-tog]")) return onFilterClick(e);
  const s = e.target.closest("[data-sel]");
  if (s) return select(s.dataset.sel, { reveal: true });
  const li = e.target.closest("#ptIdx li");
  if (li) return select(li.dataset.id);
  const f = e.target.closest("[data-full]");
  if (f) return openPortalDetail(f.dataset.full);
  const r = e.target.closest("[data-res]");
  if (r) return openResourceDetail(r.dataset.res);
  const c = e.target.closest("[data-catalog]");
  if (c) return go("catalog", { source: c.dataset.catalog });
  const t = e.target.closest("[data-tree]");
  if (t) go("tree", { axis: "source", node: t.dataset.tree });
});
el.crumbs.addEventListener("click", (e) => { const b = e.target.closest("[data-sel]"); if (b) select(b.dataset.sel, { reveal: true }); });

const renderAxes = () => { el.axes.innerHTML = AXES.map(([a, t]) => `<button type="button" class="${S.axis === a ? "on" : ""}" data-axis="${a}">${t}</button>`).join(""); };
el.axes.addEventListener("click", (e) => {
  const b = e.target.closest("[data-axis]");
  if (!b || b.dataset.axis === S.axis) return;
  S.axis = b.dataset.axis;
  S.sel = "";
  remember([]);
  renderAxes();
  applyFilters();
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
    el.results.innerHTML = results.length
      ? `<ul class="plain">${results.map((x, i) => `<li><div class="pt-hit-name"><strong>${esc(x.node.name)}</strong><span class="badge${LEVEL_CLS[x.node.level] || ""}">${esc(x.node.level_zh)}</span></div><small>${esc(x.path)}</small>
        <div class="row-foot"><button type="button" class="tnode-go" data-locate="${i}">在树中定位</button><button type="button" class="tnode-go" data-detail="${i}">详情</button>${x.node.home_url ? extLink(x.node.home_url, "官网", "tnode-go") : ""}</div></li>`).join("")}</ul><button type="button" class="link-btn" data-close="1">收起搜索结果</button>`
      : '<p class="sheet-note">没有找到匹配的门户，可以换个说法。</p>';
  } catch (err) { el.results.innerHTML = `<p class="sheet-note">${esc(err.message)}</p>`; }
});

el.results.addEventListener("click", async (e) => {
  if (e.target.closest("[data-close]")) { el.results.hidden = true; return; }
  const d = e.target.closest("[data-detail]");
  if (d) return openPortalDetail(results[Number(d.dataset.detail)].node.portal_id);
  const b = e.target.closest("[data-locate]");
  if (!b) return;
  const hit = results[Number(b.dataset.locate)];
  if (S.axis !== "geo" || Object.values(S.f).some(Boolean)) { // 定位按"地域"树、不带筛选
    S.axis = "geo"; S.f = { ...EMPTY_F }; renderAxes(); await startTree();
  }
  for (let i = 0; i < hit.trail.length; i++) {
    const li = q1(hit.trail[i]);
    if (!li) break;
    const last = i === hit.trail.length - 1;
    await expand(li, last && !q1(hit.node.id) ? { focus: hit.node.portal_id } : {});
  }
  await select(hit.node.id, { reveal: true, force: true });
});

/* ───────── 入口 ───────── */
async function restore() {
  let path = [];
  if (S.sel) path = (await api("/api/portal-tree/summary", { node: S.sel, ...fp() }).catch(() => ({ path: [] }))).path;
  const target = path.at(-1)?.id || "";
  const chain = path.map((p) => p.id);
  for (const id of [...saved(), ...chain]) {
    const li = q1(id);
    if (!li || !li.hasAttribute("aria-expanded") || id === target) continue;
    // 选中的是门户而它不在第一页时，最后一级用 focus 翻到它所在的那一页
    const last = chain.at(-2) === id && target.startsWith("p:") && !q1(target);
    if (last) await loadAndOpen(li, target.slice(2));
    else if (!isOpen(li)) await expand(li);
  }
}

async function loadAndOpen(li, portalId) {
  li.setAttribute("aria-expanded", "true");
  kidsOf(li).hidden = false;
  await loadKids(li, { focus: portalId });
  setOpen(li.dataset.id, true);
}

async function startTree() {
  nodes.clear();
  el.tree.innerHTML = '<ul class="pt-kids pt-root" role="tree" aria-label="数据门户"></ul>';
  if (await loadKids(null)) {
    await restore();
    markSel();
    const first = el.tree.querySelector(".pt-row");
    if (first) first.tabIndex = 0;
  }
}

export async function enter() {
  document.title = "门户树 · DataFinder";
  const p = currentParams();
  S.sel = p.sel || "";
  S.axis = AXES.some(([a]) => a === p.axis) ? p.axis : "geo";
  S.f = { kind: p.kind || "", official: p.official === "1", api: p.api === "1", indexed: p.indexed === "1" };
  renderAxes();
  renderFilters(S.kinds);
  await startTree();
  select(S.sel, { force: true });
}
