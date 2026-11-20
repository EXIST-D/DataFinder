"use strict";
/* 轻量路由：视图由 ?v= 决定（缺省为搜索），其余参数归各视图自己解释，状态全部写进 URL，可收藏、可前进后退。 */

const views = {}; // name -> { enter(params) }

export function register(name, mod) {
  views[name] = mod;
}

export function currentParams() {
  const sp = new URLSearchParams(location.search);
  const params = Object.fromEntries(sp.entries());
  params.v = params.v || "search";
  return params;
}

/** 更新 URL 参数（不触发视图切换）；值为空则删除该参数。 */
export function updateUrl(patch, { push = false } = {}) {
  const sp = new URLSearchParams(location.search);
  for (const [k, v] of Object.entries(patch)) {
    if (v === undefined || v === null || v === "" || v === false) sp.delete(k);
    else sp.set(k, v === true ? "1" : v);
  }
  const qs = sp.toString();
  const url = location.pathname + (qs ? "?" + qs : "");
  (push ? history.pushState : history.replaceState).call(history, null, "", url);
}

function show(name) {
  document.body.dataset.view = name;
  document.querySelectorAll(".view").forEach((el) => { el.hidden = el.dataset.view !== name; });
  document.querySelectorAll(".tabs a").forEach((a) => {
    a.classList.toggle("on", a.dataset.view === name);
    if (a.dataset.view === name) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
  });
}

/** 切换视图。`params` 会整体替换 URL 查询串（除 v 外）。 */
export function go(name, params = {}, { push = true } = {}) {
  const sp = new URLSearchParams();
  if (name !== "search") sp.set("v", name);
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== "" && v !== false) sp.set(k, v === true ? "1" : v);
  }
  const qs = sp.toString();
  (push ? history.pushState : history.replaceState).call(history, null, "", location.pathname + (qs ? "?" + qs : ""));
  render();
  window.scrollTo({ top: 0 });
}

export function render() {
  const params = currentParams();
  const name = views[params.v] ? params.v : "search";
  show(name);
  views[name]?.enter(params);
}

/** 拦截站内导航链接（带 data-view 的 <a>），不整页刷新。 */
export function bindLinks() {
  document.addEventListener("click", (e) => {
    const a = e.target.closest("a[data-view]");
    if (!a || e.metaKey || e.ctrlKey || e.shiftKey || e.button !== 0) return;
    e.preventDefault();
    const url = new URL(a.href, location.href);
    go(a.dataset.view, Object.fromEntries([...url.searchParams.entries()].filter(([k]) => k !== "v")));
  });
  window.addEventListener("popstate", render);
}
