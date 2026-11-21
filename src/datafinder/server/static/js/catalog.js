"use strict";
/* 资源目录：主题 × 地域 × 资源类型 × 获取方式。状态全部写进 URL；网页只展示"怎么获取"，不执行下载。 */

import { $, api, copy, emptyPanel, esc, ICON_OUT, safeUrl, skeleton } from "./util.js";
import { currentParams, go, updateUrl } from "./router.js";
import { openSheet, sheetBody } from "./sheet.js";

const el = { form: $("cForm"), q: $("cQ"), topics: $("cTopics"), filters: $("cFilters"), total: $("cTotal"), chips: $("cChips"), list: $("cList"), pager: $("cPager"), toggle: $("cFilterToggle"), toTree: $("cToTree") };
const PAGE = 20;
const DELIVERY = { download: ["可下载", "dl-download"], generate: ["需生成", "dl-generate"], link: ["仅链接", "dl-link"] };
const USABILITY = { image: "图片，需 OCR", document: "文档", html_table: "网页表格", tabular: "可直接分析" };
const EMPTY = { q: "", topic: "", region: "", kind: "", delivery: "", domain: "", source: "", strict: false, page: 1 };
let state = { ...EMPTY };
let vocab = null;
let seq = 0;

const fromParams = (p) => ({ q: p.q || "", topic: p.topic || "", region: p.region || "", kind: p.kind || "", delivery: p.delivery || "", domain: p.domain || "", source: p.source || "", strict: p.strict === "1", page: Math.max(1, parseInt(p.page || "1", 10) || 1) });
const syncUrl = () => updateUrl({ v: "catalog", q: state.q, topic: state.topic, region: state.region, kind: state.kind, delivery: state.delivery, domain: state.domain, source: state.source, strict: state.strict, page: state.page > 1 ? state.page : "" });

function change(patch) {
  state = { ...state, ...patch, page: patch.page ?? 1 };
  syncUrl();
  load();
}

const topicName = (id) => vocab?.topics.find((t) => t.id === id)?.name || id;
const regionName = (id) => vocab?.regions.find((r) => r.id === id)?.name || id;
const GRAN = { country: "国家级", province: "省级", prefecture: "地市级", county: "县级" };
const EVIDENCE = { structure: "来源结构", inferred: "标题推断", verified: "已验证" };

function badge(it) {
  const [text, cls] = DELIVERY[it.delivery];
  return `<span class="badge ${cls}">${text}</span>${it.usability && it.delivery !== "generate" ? `<span class="badge soft">${esc(USABILITY[it.usability] || it.usability)}</span>` : ""}`;
}

function span(it) {
  const p = it.period;
  if (p.start) return `${p.start}–${p.ongoing ? "至今" : p.end}`;
  if (it.versions?.length > 1) return `${Math.min(...it.versions)}–${Math.max(...it.versions)} 共 ${it.versions.length} 版`;
  return p.edition_year ? `${p.edition_year} 版` : "";
}

function row(it, i) {
  const topics = it.topic_ids.filter((t) => t.split(".").length === 2).slice(0, 3).map((t) => `<span class="pill">${esc(topicName(t))}</span>`).join("");
  const warn = it.missing_requirements.slice(0, 1).map((m) => `<p class="prow-note">${esc(m)}</p>`).join("");
  const meta = [it.kind, it.source_id, span(it), it.unit].filter(Boolean).map((x) => `<span>${esc(x)}</span>`).join("");
  return `<li class="prow clickable" data-i="${i}" tabindex="0" role="button">
    <div class="prow-main">
      <div class="prow-title">${esc(it.title)}<span class="portal-badges">${badge(it)}</span></div>
      <div class="facts">${meta}</div>
      ${it.path ? `<p class="desc">${esc(it.path)}</p>` : ""}${warn}
      <div class="row-foot">${topics}</div>
    </div></li>`;
}

/* ───────── 筛选区 ───────── */
function radio(name, title, items, selected, all = "全部") {
  const rows = items.map((x) => `<label class="opt${x.count === 0 && selected !== x.id ? " dis" : ""}"><input type="radio" name="${name}" value="${esc(x.id)}"${selected === x.id ? " checked" : ""}><span class="opt-name">${esc(x.name)}</span><span class="opt-n">${x.count.toLocaleString("zh-CN")}</span></label>`).join("");
  return `<fieldset class="fgroup"><legend>${esc(title)}</legend><label class="opt"><input type="radio" name="${name}" value=""${selected ? "" : " checked"}><span class="opt-name">${esc(all)}</span></label>${rows}</fieldset>`;
}

function renderFilters(f) {
  const regions = f.regions.map((r) => ({ id: r.id, name: r.name, count: r.count }));
  el.filters.innerHTML = [
    radio("delivery", "获取方式", f.delivery, state.delivery),
    radio("kind", "资源类型", f.kinds, state.kind),
    `<fieldset class="fgroup"><legend>地域</legend><select class="more-select" id="cRegion" aria-label="地域"><option value="">全部地域</option>${regions.map((r) => `<option value="${esc(r.id)}"${state.region === r.id ? " selected" : ""}>${esc(r.name)}（${r.count}）</option>`).join("")}</select></fieldset>`,
    `<fieldset class="fgroup"><legend>匹配方式</legend><label class="opt"><input type="checkbox" id="cStrict"${state.strict ? " checked" : ""}><span class="opt-name">严格模式 <small>信息未知/未验证的不算满足</small></span></label></fieldset>`,
  ].join("");
}

function renderTopics(f) {
  const l1 = f.topics.filter((t) => t.level === 1).sort((a, b) => b.count - a.count);
  el.topics.innerHTML = l1.map((t) => `<button type="button" class="tile${state.topic === t.id ? " on" : ""}${t.count === 0 && state.topic !== t.id ? " dim" : ""}" data-topic="${esc(t.id)}" aria-pressed="${state.topic === t.id}"><span class="tile-name">${esc(t.name.replace(/与.*$/, ""))}</span><span class="tile-count">${t.count.toLocaleString("zh-CN")}</span></button>`).join("");
}

function renderChips() {
  const chips = [];
  if (state.topic) chips.push(["topic", `主题：${topicName(state.topic)}`]);
  if (state.region) chips.push(["region", `地域：${regionName(state.region)}`]);
  if (state.kind) chips.push(["kind", `类型：${vocab.kinds.find((k) => k.id === state.kind)?.name || state.kind}`]);
  if (state.delivery) chips.push(["delivery", DELIVERY[state.delivery][0]]);
  if (state.domain) chips.push(["domain", `领域：${state.domain}`]);
  if (state.source) chips.push(["source", `来源：${state.source}`]);
  if (state.strict) chips.push(["strict", "严格模式"]);
  el.chips.innerHTML = chips.map(([k, t]) => `<button type="button" class="achip" data-clear="${k}">${esc(t)} <span aria-hidden="true">×</span><span class="sr">移除</span></button>`).join("");
}

function renderPager(total) {
  const pages = Math.max(1, Math.ceil(total / PAGE));
  if (pages <= 1) { el.pager.innerHTML = ""; return; }
  const cur = state.page;
  const seqn = [...new Set([1, pages, cur - 1, cur, cur + 1])].filter((n) => n >= 1 && n <= pages).sort((a, b) => a - b);
  let html = `<button type="button" data-page="${cur - 1}"${cur === 1 ? " disabled" : ""}>上一页</button>`;
  let prev = 0;
  for (const n of seqn) { if (n - prev > 1) html += '<span class="gap">…</span>'; html += `<button type="button" data-page="${n}"${n === cur ? ' class="on" aria-current="page"' : ""}>${n}</button>`; prev = n; }
  el.pager.innerHTML = html + `<button type="button" data-page="${cur + 1}"${cur === pages ? " disabled" : ""}>下一页</button>`;
}

let current = [];

async function load() {
  const mine = ++seq;
  el.list.innerHTML = skeleton(4);
  try {
    if (!vocab) vocab = await api("/api/catalog/facets");
    const res = await api("/api/catalog/search", {
      q: state.q, topic: state.topic, region: state.region, kind: state.kind, delivery: state.delivery, domain: state.domain, source: state.source,
      mode: state.strict ? "strict" : "discovery", limit: PAGE, offset: (state.page - 1) * PAGE,
    });
    if (mine !== seq) return;
    current = res.results;
    renderTopics(res.facets); renderFilters(res.facets); renderChips();
    el.total.textContent = `${res.total.toLocaleString("zh-CN")} 条候选`;
    el.list.innerHTML = res.results.length
      ? `<ol class="panel plist">${res.results.map(row).join("")}</ol>` + (res.hints.length ? `<p class="note">${esc(res.hints[0])}</p>` : "")
      : `<div class="panel"><div class="empty"><h3>没有符合条件的资源</h3><p>${esc(res.hints[0] || "可以放宽筛选，或换个关键词。")}</p><p><button type="button" class="chip" data-clear="*">清除全部筛选</button></p></div></div>`;
    renderPager(res.total);
  } catch (e) {
    if (mine !== seq) return;
    el.list.innerHTML = emptyPanel(e.status === 503 ? "资源目录还没建立" : "加载失败", e.message);
  }
}

/* ───────── 详情面板 ───────── */
function cliHint(r, how) {
  if (how.tool === "fetch") return `datafinder get '${r.resource_id}' ${how.needs.includes("regions") ? "-r 河北 " : ""}${how.needs.includes("indicators") ? "-i 指标名 " : ""}--yes`;
  if (how.tool === "download") return `datafinder get '${r.resource_id}' --yes`;
  return "";
}

export async function openDetail(id) {
  openSheet((b) => { b.innerHTML = '<p class="sheet-note"><span class="spinner"></span>读取中…</p>'; });
  try {
    const d = await api("/api/catalog/inspect", { id, children: true });
    const r = d.resource;
    const [dtext, dcls] = DELIVERY[r.delivery];
    const dists = d.distributions.map((x) => `<li><span class="badge soft">${esc(x.access_method)}</span> ${x.url ? `<a href="${esc(safeUrl(x.url))}" target="_blank" rel="noopener noreferrer">${esc(x.url)} ${ICON_OUT}</a>` : ""} ${x.file_format ? `<span class="pill">${esc(x.file_format)}</span>` : ""} ${x.usability ? `<span class="pill">${esc(USABILITY[x.usability] || x.usability)}</span>` : ""}${x.note ? ` <small>${esc(x.note)}</small>` : ""}</li>`).join("");
    const kids = (d.children || []).map((c) => `<li class="clickable" data-open="${esc(c.resource_id)}" tabindex="0" role="button"><span class="badge ${DELIVERY[c.delivery][1]}">${DELIVERY[c.delivery][0]}</span> ${esc(c.title)}</li>`).join("");
    const terms = (d.glossary || []).map((t) => `<li><strong>${esc(t.name_zh)}</strong>（${esc(t.status_zh)}）${esc(t.definition)} <button class="link-btn" type="button" data-gterm="${esc(t.name_zh)}">术语词典 ›</button></li>`).join("");
    const portal = d.source?.portal;
    const cmd = cliHint(r, d.how_to_get);
    const regions = r.regions.map((x) => `${esc(regionName(x.region_id))}${x.granularity ? `·按${esc(GRAN[x.granularity] || x.granularity)}报告` : ""}（${esc(EVIDENCE[x.evidence] || x.evidence)}）`).join("；");
    openSheet((b) => {
      b.innerHTML = `
        <p class="sheet-note">${d.parents.map((p) => esc(p.title)).join(" › ")}</p>
        <h3 id="sheetTitle">${esc(r.title)}</h3>
        <div class="sheet-badges"><span class="badge ${dcls}">${dtext}</span><span class="pill">${esc(r.kind)}</span><span class="pill">${esc(r.source_id)}</span><span class="pill">核验：${esc(d.verification_level)}</span></div>
        <dl class="facts-grid">
          ${r.unit ? `<dt>单位</dt><dd>${esc(r.unit)}</dd>` : ""}
          <dt>观测期</dt><dd>${esc(span(r) || "未知")}${r.period.edition_year ? `<small>出版年 ${r.period.edition_year}（不等于观测期）</small>` : ""}</dd>
          <dt>频率</dt><dd>${esc(r.frequency || "未知")}</dd>
          <dt>地域</dt><dd>${regions || "未标注"}</dd>
          <dt>主题</dt><dd>${r.topic_ids.map((t) => esc(topicName(t))).join("、") || "未分类"}</dd>
        </dl>
        ${terms ? `<h4>术语说明</h4><ul class="plain">${terms}</ul>` : ""}
        ${portal ? `<p class="sheet-note">来源：${esc(d.source.name)} · <button class="link-btn" type="button" data-gsource="${esc(d.source.name)}">在“数据来源”里查看 ›</button></p>` : ""}
        <h4>怎么获取</h4>
        <p class="lead">${esc(d.how_to_get.note)}</p>
        ${cmd ? `<pre class="code" id="cCmd">${esc(cmd)}</pre><div class="res-actions"><button class="link-btn" id="cCopy" type="button">复制命令</button></div>` : ""}
        ${dists ? `<h4>入口</h4><ul class="plain">${dists}</ul>` : ""}
        ${kids ? `<h4>下级资源（${d.child_count}）</h4><ul class="plain">${kids}</ul>` : ""}`;
      b.querySelector("#cCopy")?.addEventListener("click", () => copy(cmd));
    });
  } catch (e) {
    openSheet((b) => { b.innerHTML = `<h3 id="sheetTitle">读取失败</h3><p class="sheet-note">${esc(e.message)}</p>`; });
  }
}

/* ───────── 事件 ───────── */
el.form.addEventListener("submit", (e) => { e.preventDefault(); change({ q: el.q.value.trim() }); });
el.topics.addEventListener("click", (e) => { const b = e.target.closest("[data-topic]"); if (b) change({ topic: state.topic === b.dataset.topic ? "" : b.dataset.topic }); });
el.filters.addEventListener("change", (e) => {
  const t = e.target;
  if (t.name === "delivery" || t.name === "kind") change({ [t.name]: t.value });
  else if (t.id === "cRegion") change({ region: t.value });
  else if (t.id === "cStrict") change({ strict: t.checked });
});
el.toTree.addEventListener("click", () => go("tree", { q: state.q, topic: state.topic, region: state.region, kind: state.kind, delivery: state.delivery, domain: state.domain, source: state.source, strict: state.strict }));
el.toggle.addEventListener("click", () => { const open = el.filters.classList.toggle("open"); el.toggle.setAttribute("aria-expanded", String(open)); });
el.list.addEventListener("click", (e) => {
  const c = e.target.closest("[data-clear]");
  if (c) return change({ ...EMPTY, q: state.q });
  const r = e.target.closest("[data-i]");
  if (r) openDetail(current[Number(r.dataset.i)].resource_id);
});
el.list.addEventListener("keydown", (e) => { if (e.key === "Enter") e.target.closest("[data-i]")?.click(); });
el.chips.addEventListener("click", (e) => { const c = e.target.closest("[data-clear]"); if (c) change({ [c.dataset.clear]: c.dataset.clear === "strict" ? false : "" }); });
el.pager.addEventListener("click", (e) => { const p = e.target.closest("[data-page]"); if (p && !p.disabled) { state.page = Number(p.dataset.page); syncUrl(); load(); window.scrollTo({ top: el.topics.offsetTop - 12, behavior: "smooth" }); } });
sheetBody.addEventListener("click", (e) => {
  const k = e.target.closest("[data-open]");
  if (k) return openDetail(k.dataset.open);
  const g = e.target.closest("[data-gterm]");
  if (g) return go("glossary", { q: g.dataset.gterm });
  const s = e.target.closest("[data-gsource]");
  if (s) go("sources", { q: s.dataset.gsource });
});
sheetBody.addEventListener("keydown", (e) => { if (e.key === "Enter") e.target.closest("[data-open]")?.click(); });

export function enter() {
  document.title = "资源目录 · DataFinder";
  state = fromParams(currentParams());
  el.q.value = state.q;
  load();
}
