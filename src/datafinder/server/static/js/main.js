"use strict";
/* 入口：注册各视图、绑定站内导航、读取状态。 */

import { $, api } from "./util.js";
import { bindLinks, register, render } from "./router.js";
import * as search from "./search.js";
import * as browse from "./browse.js";
import * as catalog from "./catalog.js";
import * as tree from "./tree.js";
import * as sources from "./sources.js";
import * as ptree from "./ptree.js";
import * as glossary from "./glossary.js";
import * as china from "./china.js";
import * as links from "./links.js";
import "./sheet.js";

register("search", search);
register("catalog", catalog);
register("tree", tree);
register("sources", sources);
register("ptree", ptree);
register("glossary", glossary);
register("browse", browse);
register("china", china);
register("links", links);
bindLinks();

(async function loadStatus() {
  try {
    const s = await api("/api/status");
    search.setInspectAvailable(s.dataseek);
    $("navStatus").textContent = s.portals ? `门户库 ${s.portals.toLocaleString("zh-CN")} 条` : "门户库为空 · 请先运行 datafinder sync";
  } catch { /* 状态只是装饰 */ }
})();

render();
