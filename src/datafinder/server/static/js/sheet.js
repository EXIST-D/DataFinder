"use strict";
/* 详情面板（浮层）：右侧滑出，手机端为底部抽屉。内容由各视图通过 openSheet(render) 注入。 */

import { $ } from "./util.js";

const scrim = $("scrim");
const sheet = $("sheet");
const body = $("sheetBody");
let lastFocus = null;

export function openSheet(render) {
  lastFocus = document.activeElement;
  body.scrollTop = 0;
  render(body);
  scrim.hidden = false;
  sheet.hidden = false;
  requestAnimationFrame(() => {
    scrim.classList.add("on");
    sheet.classList.add("on");
  });
  $("sheetClose").focus({ preventScroll: true });
  document.body.style.overflow = "hidden";
}

export function closeSheet() {
  if (sheet.hidden) return;
  scrim.classList.remove("on");
  sheet.classList.remove("on");
  document.body.style.overflow = "";
  setTimeout(() => { scrim.hidden = true; sheet.hidden = true; }, 420);
  lastFocus?.focus?.({ preventScroll: true });
}

export const sheetBody = body;

$("sheetClose").addEventListener("click", closeSheet);
scrim.addEventListener("click", closeSheet);
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeSheet(); });
