"use strict";
/* 分类浏览：领域磁贴 + 左侧筛选（带条数）+ 门户列表 + 分页。状态全部写进 URL。 */

import { $, api, emptyPanel, esc, ICON_OUT, portalBadges, safeUrl, skeleton, verifyBadge } from "./util.js";
import { currentParams, updateUrl } from "./router.js";

const el = {
  domains: $("domains"), filters: $("filters"), total: $("bTotal"), chips: $("activeChips"),
  sort: $("bSort"), list: $("bList"), pager: $("pager"),
};
const PAGE_SIZE = 20;
const TOP_COUNTRIES = 10;

const EMPTY = { domain: "", country: "", region: "", tiers: [], kind: "", openness: "", api: false, official: false, sort: "official", page: 1 };
let state = { ...EMPTY };
let facets = null;
let reqSeq = 0;

/* ───────── 状态 <-> URL ───────── */
function fromParams(p) {
  return {
    domain: p.domain || "",
    country: p.country || "",
    region: p.region || "",
    tiers: (p.tiers || "").split(",").filter(Boolean).map(Number).filter((n) => n >= 1 && n <= 6),
    kind: p.kind || "",
    openness: p.openness || "",
    api: p.api === "1" || p.api === "true",
    official: p.official === "1" || p.official === "true",
    sort: p.sort === "name" ? "name" : "official",
    page: Math.max(1, parseInt(p.page || "1", 10) || 1),
  };
}

function toQuery(s, { withPaging = false } = {}) {
  const q = {
    domain: s.domain, country: s.country, region: s.region,
    tiers: s.tiers.join(","), kind: s.kind, openness: s.openness,
    api: s.api || "", official: s.official || "",
  };
  if (withPaging) { q.sort = s.sort; q.page = s.page; q.size = PAGE_SIZE; }
  return q;
}

function syncUrl() {
  updateUrl({
    v: "browse", domain: state.domain, country: state.country, region: state.region,
    tiers: state.tiers.join(","), kind: state.kind, openness: state.openness,
    api: state.api, official: state.official,
    sort: state.sort === "official" ? "" : state.sort, page: state.page > 1 ? state.page : "",
    q: "", db: "",
  });
}

function change(patch) {
  state = { ...state, ...patch, page: patch.page ?? 1 };
  // 换国家后，省份不再有意义
  if ("country" in patch && !("region" in patch)) state.region = "";
  syncUrl();
  load();
}

/* ───────── 渲染 ───────── */
function domainTiles() {
  el.domains.innerHTML = facets.domains.map((d) => `
    <button type="button" class="tile${state.domain === d.id ? " on" : ""}${d.count === 0 && state.domain !== d.id ? " dim" : ""}" data-domain="${esc(d.id)}" aria-pressed="${state.domain === d.id}">
      <span class="tile-name">${esc(d.zh)}</span><span class="tile-count">${d.count.toLocaleString("zh-CN")}</span>
    </button>`).join("");
}

function radioGroup(name, title, items, selected, { allLabel = "全部", extra = "" } = {}) {
  const rows = items.map((it) => {
    const on = String(selected) === String(it.id);
    const dis = it.count === 0 && !on;
    return `<label class="opt${dis ? " dis" : ""}"><input type="radio" name="${name}" value="${esc(it.id)}"${on ? " checked" : ""}${dis ? " disabled" : ""}>
      <span class="opt-name">${esc(it.zh)}</span><span class="opt-n">${it.count.toLocaleString("zh-CN")}</span></label>`;
  }).join("");
  return `<fieldset class="fgroup"><legend>${esc(title)}</legend>
    <label class="opt"><input type="radio" name="${name}" value=""${selected ? "" : " checked"}><span class="opt-name">${esc(allLabel)}</span></label>
    ${rows}${extra}</fieldset>`;
}

function countryGroup() {
  const all = facets.countries;
  const top = all.slice(0, TOP_COUNTRIES);
  const selected = all.find((c) => c.id === state.country);
  // 中国始终置顶（项目以中文数据为主），选中的国家如果不在前 N 也要显示
  const cn = all.find((c) => c.id === "CN");
  const shownList = [...(cn ? [cn] : []), ...top.filter((c) => c.id !== "CN"), ...(selected && !top.includes(selected) && selected.id !== "CN" ? [selected] : [])];
  const rest = all.filter((c) => !shownList.includes(c));
  const more = rest.length
    ? `<select class="more-select" id="moreCountry" aria-label="更多国家和地区"><option value="">更多国家/地区（${rest.length}）…</option>${rest.map((c) => `<option value="${esc(c.id)}">${esc(c.zh)}（${c.count}）</option>`).join("")}</select>`
    : "";
  let html = radioGroup("country", "国家 / 地区", shownList, state.country, { allLabel: "全部地区", extra: more });
  if (state.country && facets.regions.length > 1) {
    html += `<fieldset class="fgroup"><legend>省 / 州</legend>
      <select class="more-select" id="regionSel" aria-label="省份">
        <option value="">全部（${facets.regions.reduce((s, r) => s + r.count, 0)}）</option>
        ${facets.regions.map((r) => `<option value="${esc(r.id)}"${state.region === r.id ? " selected" : ""}>${esc(r.zh)}（${r.count}）</option>`).join("")}
      </select></fieldset>`;
  }
  return html;
}

function tierGroup() {
  const rows = facets.tiers.map((t) => {
    const on = state.tiers.includes(t.id);
    const dis = t.count === 0 && !on;
    return `<label class="opt${dis ? " dis" : ""}"><input type="checkbox" name="tier" value="${t.id}"${on ? " checked" : ""}${dis ? " disabled" : ""}>
      <span class="opt-name">${esc(t.zh)}${t.official ? "" : ""}</span><span class="opt-n">${t.count.toLocaleString("zh-CN")}</span></label>`;
  }).join("");
  return `<fieldset class="fgroup"><legend>机构类型</legend>
    <label class="opt"><input type="checkbox" id="officialOnly"${state.official ? " checked" : ""}><span class="opt-name">仅官方 <small>国际组织·国家·地方政府</small></span><span class="opt-n">${facets.official.toLocaleString("zh-CN")}</span></label>
    ${rows}</fieldset>`;
}

function renderFilters() {
  const apiDis = facets.api === 0 && !state.api;
  el.filters.innerHTML = [
    countryGroup(),
    tierGroup(),
    radioGroup("kind", "数据形态", facets.kinds, state.kind),
    radioGroup("openness", "开放程度", facets.openness, state.openness),
    `<fieldset class="fgroup"><legend>获取方式</legend>
      <label class="opt${apiDis ? " dis" : ""}"><input type="checkbox" id="apiOnly"${state.api ? " checked" : ""}${apiDis ? " disabled" : ""}><span class="opt-name">提供 API 端点</span><span class="opt-n">${facets.api.toLocaleString("zh-CN")}</span></label>
    </fieldset>`,
  ].join("");
}

function labelOf(list, id) { return list.find((x) => String(x.id) === String(id))?.zh || id; }

function renderChips() {
  const chips = [];
  if (state.domain) chips.push(["domain", `领域：${labelOf(facets.domains, state.domain)}`]);
  if (state.country) chips.push(["country", `地区：${labelOf(facets.countries, state.country)}`]);
  if (state.region) chips.push(["region", `省：${labelOf(facets.regions, state.region)}`]);
  state.tiers.forEach((t) => chips.push([`tier:${t}`, labelOf(facets.tiers, t)]));
  if (state.official) chips.push(["official", "仅官方"]);
  if (state.kind) chips.push(["kind", labelOf(facets.kinds, state.kind)]);
  if (state.openness) chips.push(["openness", labelOf(facets.openness, state.openness)]);
  if (state.api) chips.push(["api", "提供 API"]);
  el.chips.innerHTML = chips.map(([k, t]) => `<button type="button" class="achip" data-clear="${esc(k)}">${esc(t)} <span aria-hidden="true">×</span><span class="sr">移除</span></button>`).join("")
    + (chips.length > 1 ? '<button type="button" class="achip clear" data-clear="*">清除全部</button>' : "");
}

function portalRow(p) {
  const place = [p.country, p.region].filter(Boolean).join("·");
  const meta = [place, p.org_type, p.kind, p.openness && `${p.openness}`].filter(Boolean).map((t) => `<span>${esc(t)}</span>`).join("");
  const tags = p.industries.slice(0, 4).map((t) => `<span class="pill">${esc(t)}</span>`).join("");
  return `<li class="prow">
    <div class="prow-main">
      <div class="prow-title">${esc(p.name)}${p.name_en ? `<small>${esc(p.name_en)}</small>` : ""}<span class="portal-badges">${portalBadges(p)}${verifyBadge(p)}</span></div>
      <div class="facts">${meta}</div>
      ${p.note ? `<p class="prow-note">${esc(p.note)}</p>` : ""}
      ${p.description ? `<p class="desc">${esc(p.description)}</p>` : ""}
      <div class="row-foot">${tags}</div>
    </div>
    <a class="link-btn go-out" href="${esc(safeUrl(p.url))}" target="_blank" rel="noopener noreferrer" aria-label="打开 ${esc(p.name)}">打开 ${ICON_OUT}</a>
  </li>`;
}

function renderPager(total) {
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  if (pages <= 1) { el.pager.innerHTML = ""; return; }
  const cur = state.page;
  const nums = new Set([1, pages, cur - 1, cur, cur + 1]);
  const seq = [...nums].filter((n) => n >= 1 && n <= pages).sort((a, b) => a - b);
  let html = `<button type="button" data-page="${cur - 1}"${cur === 1 ? " disabled" : ""}>上一页</button>`;
  let prev = 0;
  for (const n of seq) {
    if (n - prev > 1) html += '<span class="gap">…</span>';
    html += `<button type="button" data-page="${n}"${n === cur ? ' class="on" aria-current="page"' : ""}>${n}</button>`;
    prev = n;
  }
  html += `<button type="button" data-page="${cur + 1}"${cur === pages ? " disabled" : ""}>下一页</button>`;
  el.pager.innerHTML = html;
}

/* ───────── 加载 ───────── */
async function load() {
  const mine = ++reqSeq;
  el.list.innerHTML = skeleton(4);
  try {
    const [fx, res] = await Promise.all([
      api("/api/browse/facets", toQuery(state)),
      api("/api/browse/portals", toQuery(state, { withPaging: true })),
    ]);
    if (mine !== reqSeq) return;
    if (fx.synced === false) {
      el.list.innerHTML = emptyPanel("门户库还是空的", "请在终端运行 datafinder sync 后刷新页面。");
      el.filters.innerHTML = ""; el.domains.innerHTML = ""; el.total.textContent = ""; el.chips.innerHTML = ""; el.pager.innerHTML = "";
      return;
    }
    facets = fx;
    el.sort.value = state.sort;
    domainTiles(); renderFilters(); renderChips();
    el.total.textContent = `${fx.total.toLocaleString("zh-CN")} 个门户`;
    if (!res.portals.length) {
      el.list.innerHTML = `<div class="panel"><div class="empty"><h3>没有符合条件的门户</h3><p>可以放宽条件试试。</p><p><button type="button" class="chip" data-clear="*">清除全部筛选</button></p></div></div>`;
    } else {
      el.list.innerHTML = `<ol class="panel plist">${res.portals.map(portalRow).join("")}</ol>`;
    }
    renderPager(res.total);
  } catch (e) {
    if (mine !== reqSeq) return;
    el.list.innerHTML = emptyPanel("加载失败", e.message);
  }
}

/* ───────── 事件 ───────── */
el.domains.addEventListener("click", (e) => {
  const b = e.target.closest("[data-domain]");
  if (b) change({ domain: state.domain === b.dataset.domain ? "" : b.dataset.domain });
});

el.filters.addEventListener("change", (e) => {
  const t = e.target;
  if (t.name === "country") change({ country: t.value, region: "" });
  else if (t.id === "moreCountry") change({ country: t.value, region: "" });
  else if (t.id === "regionSel") change({ region: t.value });
  else if (t.name === "kind") change({ kind: t.value });
  else if (t.name === "openness") change({ openness: t.value });
  else if (t.id === "officialOnly") change({ official: t.checked });
  else if (t.id === "apiOnly") change({ api: t.checked });
  else if (t.name === "tier") {
    const id = Number(t.value);
    change({ tiers: t.checked ? [...state.tiers, id] : state.tiers.filter((x) => x !== id) });
  }
});

document.addEventListener("click", (e) => {
  if (document.body.dataset.view !== "browse") return;
  const c = e.target.closest("[data-clear]");
  if (c) {
    const k = c.dataset.clear;
    if (k === "*") change({ ...EMPTY, sort: state.sort });
    else if (k.startsWith("tier:")) change({ tiers: state.tiers.filter((x) => x !== Number(k.slice(5))) });
    else if (k === "api" || k === "official") change({ [k]: false });
    else change({ [k]: "", ...(k === "country" ? { region: "" } : {}) });
    return;
  }
  const p = e.target.closest("[data-page]");
  if (p && !p.disabled) {
    state.page = Number(p.dataset.page);
    syncUrl(); load(); window.scrollTo({ top: el.domains.offsetTop - 12, behavior: "smooth" });
  }
});

const toggle = $("filterToggle");
toggle.addEventListener("click", () => {
  const open = el.filters.classList.toggle("open");
  toggle.setAttribute("aria-expanded", String(open));
});

el.sort.addEventListener("change", () => change({ sort: el.sort.value }));

export function enter() {
  document.title = "分类浏览 · DataFinder";
  state = fromParams(currentParams());
  load();
}
