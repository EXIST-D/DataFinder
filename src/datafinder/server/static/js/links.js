"use strict";
/* 检索入口：外部数据检索引擎清单，带着关键词一键跳转。 */

import { $, api, emptyPanel, esc, ICON_OUT, safeUrl, skeleton } from "./util.js";
import { updateUrl } from "./router.js";

const el = { form: $("linksForm"), q: $("linksQ"), groups: $("linksGroups"), hint: $("linksHint") };
let timer;
let seq = 0;

function linkCard(l) {
  const warn = l.note ? `<p class="link-note">${esc(l.note)}</p>` : "";
  return `<a class="lcard" href="${esc(safeUrl(l.url))}" target="_blank" rel="noopener noreferrer">
    <span class="lcard-top"><span class="lcard-name">${esc(l.name)}</span><span class="link-out">${ICON_OUT}</span></span>
    <span class="lcard-desc">${esc(l.desc || "")}</span>
    <span class="lcard-meta"><span class="pill">${esc(l.scope || "")}</span>${l.query ? `<span class="lcard-q">搜“${esc(l.query)}”</span>` : ""}</span>
    ${warn}</a>`;
}

function render(data) {
  el.hint.textContent = data.query
    ? "国际检索引擎用的是我们翻译好的英文检索词，中文引擎用你输入的原话。"
    : "输入关键词后，每个入口都会带着它跳转；不输入则只打开各站首页式的搜索页。";
  el.groups.innerHTML = data.groups.map((g) => `
    <section class="block"><div class="block-head"><h2>${esc(g.zh)}</h2></div>
      <div class="lgrid">${g.links.map(linkCard).join("")}</div></section>`).join("");
}

async function load(q) {
  const mine = ++seq;
  el.groups.innerHTML = skeleton(2);
  try {
    const data = await api("/api/links", { q });
    if (mine === seq) render(data);
  } catch (e) {
    if (mine === seq) el.groups.innerHTML = emptyPanel("加载失败", e.message);
  }
}

el.q.addEventListener("input", () => {
  clearTimeout(timer);
  timer = setTimeout(() => { updateUrl({ v: "links", q: el.q.value.trim() }); load(el.q.value.trim()); }, 300);
});
el.form.addEventListener("submit", (e) => { e.preventDefault(); clearTimeout(timer); load(el.q.value.trim()); });

export function enter(params) {
  document.title = "检索入口 · DataFinder";
  const q = (params.q || "").trim();
  el.q.value = q;
  load(q);
}
