"use strict";
/* 通用小工具：转义、安全链接、请求、复制、提示。所有动态文本进 HTML 前必须过 esc()。 */

export const $ = (id) => document.getElementById(id);

export function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

/** 只允许 http(s) 链接，其余一律换成 "#"，防止 javascript: 之类的地址。 */
export function safeUrl(u) {
  try {
    const x = new URL(u);
    return x.protocol === "http:" || x.protocol === "https:" ? x.href : "#";
  } catch {
    return "#";
  }
}

export const ICON_OUT = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M7 17L17 7M8 7h9v9"/></svg>';
export const ICON_CHEVRON = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M9 6l6 6-6 6"/></svg>';

export function extLink(href, text, cls = "link-btn") {
  return `<a class="${cls}" href="${esc(safeUrl(href))}" target="_blank" rel="noopener noreferrer">${esc(text)} ${ICON_OUT}</a>`;
}

export function fmtSize(n) {
  if (!n) return "";
  const u = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return (i === 0 ? n : n.toFixed(1)) + " " + u[i];
}

/** GET JSON。失败时抛出带 status 与后端 detail 的 Error（页面据此给出明确提示）。 */
export async function api(path, params = {}) {
  const sp = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== "" && v !== false) sp.set(k, v === true ? "true" : v);
  }
  const url = sp.toString() ? `${path}?${sp}` : path;
  const r = await fetch(url);
  if (!r.ok) {
    let msg = r.statusText;
    try { const j = await r.json(); msg = typeof j.detail === "string" ? j.detail : msg; } catch { /* 保持 statusText */ }
    const e = new Error(msg);
    e.status = r.status;
    throw e;
  }
  return r.json();
}

const toastEl = () => $("toast");
let toastTimer;
export function toast(msg) {
  const el = toastEl();
  el.textContent = msg;
  el.classList.add("on");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove("on"), 1800);
}

export async function copy(text) {
  try {
    await navigator.clipboard.writeText(text);
  } catch {
    const t = document.createElement("textarea");
    t.value = text;
    t.style.position = "fixed";
    t.style.opacity = "0";
    document.body.appendChild(t);
    t.select();
    document.execCommand("copy");
    t.remove();
  }
  toast("已复制");
}

export function emptyPanel(title, text) {
  return `<div class="panel"><div class="empty">${title ? `<h3>${esc(title)}</h3>` : ""}${text ? `<p>${esc(text)}</p>` : ""}</div></div>`;
}

export function skeleton(rows = 3) {
  return `<div class="panel">${'<div class="skel"><i></i><i></i><i></i></div><div class="sep"></div>'.repeat(rows)}</div>`;
}

export function setLoading(on) {
  document.body.classList.toggle("loading", !!on);
}

/** 门户行里的徽标（精选/国家级/官方/API）。 */
export function portalBadges(p) {
  return [
    p.featured ? '<span class="badge">精选</span>' : "",
    p.national ? '<span class="badge">国家级</span>' : "",
    p.official && !p.national ? '<span class="badge soft">官方</span>' : "",
    p.api ? '<span class="badge">API</span>' : "",
  ].join("");
}

/** 自有精选的核验状态徽标：诚实标注，不把没核验的当成已核验。 */
export function verifyBadge(p) {
  if (p.verify === "blocked") return '<span class="badge warn" title="站点在线，但对自动访问返回 403/412">拒绝自动访问</span>';
  if (p.verify === "unverified") return '<span class="badge warn" title="从当前网络未能核验可访问性">未核验</span>';
  return "";
}
