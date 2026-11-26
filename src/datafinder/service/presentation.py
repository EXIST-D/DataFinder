"""汇报：把各工具的完整数据整理成"给助手看的简明文字"和统一的 `presentation` 汇报块。

设计（MCP 规范 SEP-1624、Anthropic 工具设计建议）：
- `structuredContent` = 完整数据 + `presentation`（程序/界面使用，内容不因汇报方式改变）；
- `content` = 由数据渲染的简明中文 Markdown（concise 默认，几千字符；full 给更多条目；json 给紧凑 JSON 全文）；
- `presentation` 固定四段：summary 结论、evidence 依据、gaps 缺口、next_actions 下一步，
  以及 operation_status（complete / partial / needs_confirmation / failed）。

纯函数、与领域无关：只读通用字段（resource_id / kind / title / delivery …）。
"""

from __future__ import annotations

import json
from typing import Any

from datafinder.catalog import regions

DETAILS = ("concise", "full", "json")
TEXT_LIMIT = 24000  # 单次文字上限；超出时少给条目而不是截断一半

_DELIVERY = {"download": "可下载", "generate": "可取数", "link": "仅链接"}
_USABILITY = {"tabular": "表格", "html_table": "网页表", "document": "文档", "image": "图片"}
_STAGE = {
    "located": "已定位（只有链接）",
    "previewed": "已预览（未执行）",
    "acquired": "已取回",
    "validated": "已取回并通过覆盖检查",
    "failed": "失败",
}


def normalize_detail(detail: str | None) -> str:
    d = (detail or "concise").strip().lower()
    if d not in DETAILS:
        from .errors import ServiceError

        raise ServiceError(
            f"detail 只能是 {' / '.join(DETAILS)}",
            code="INVALID_ARGUMENT",
            next_action="改用 concise",
        )
    return d


def _clip(s: Any, n: int) -> str:
    s = str(s or "").replace("\n", " ").strip()
    return s if len(s) <= n else s[: n - 1] + "…"


def _tag(item: dict) -> str:
    parts = [
        item.get("kind"),
        _DELIVERY.get(item.get("delivery") or "", item.get("delivery")),
        _USABILITY.get(item.get("usability") or "", None),
    ]
    return "·".join(p for p in parts if p)


def _where(item: dict) -> str:
    covers = [r for r in item.get("regions", []) if r.get("relation") == "covers"]
    names = [regions.name_zh(r["region_id"]) or r["region_id"] for r in covers[:3]]
    scope = "（含下级）" if covers and covers[0].get("scope_mode") == "descendants" else ""
    return ("地域：" + "、".join(names) + scope) if names else ""


def _period(item: dict) -> str:
    p = item.get("period") or {}
    if p.get("start") or p.get("end"):
        return (
            f"期别：{p.get('start') or '?'}–{p.get('end') or ('至今' if p.get('ongoing') else '?')}"
        )
    if p.get("edition_year"):
        return f"版次：{p['edition_year']}"
    return ""


def _item(i: int, item: dict, *, full: bool) -> str:
    head = f"{i}. **{_clip(item.get('title'), 60)}**〔{_tag(item)}〕"
    if item.get("unit"):
        head += f" 单位 {item['unit']}"
    second = " | ".join(
        x
        for x in (_clip(item.get("path") or item.get("source_id"), 70), _where(item), _period(item))
        if x
    )
    lines = [head]
    if second:
        lines.append(f"   {second}")
    why = "；".join(item.get("why_matched") or [])
    miss = "；".join(item.get("missing_requirements") or [])
    if full or miss:
        extra = " | ".join(
            x
            for x in (
                f"匹配：{_clip(why, 80)}" if why and full else "",
                f"缺：{_clip(miss, 80)}" if miss else "",
            )
            if x
        )
        if extra:
            lines.append(f"   {extra}")
    lines.append(f"   id: `{item.get('resource_id')}`")
    need = {
        k: v for k, v in ((item.get("fetch") or {}).get("args") or {}).items() if k != "resource_id"
    }
    if need:
        lines.append("   fetch 需要：" + "；".join(f"{k}={v}" for k, v in need.items()))
    return "\n".join(lines)


def _fit(blocks: list[str]) -> str:
    """按块拼接，超过上限就少给后面的块（不在块中间截断）。"""
    out, size = [], 0
    for b in blocks:
        if size + len(b) > TEXT_LIMIT and out:
            out.append("（输出过长，后面的内容已省略；缩小范围或用 limit/offset 分页）")
            break
        out.append(b)
        size += len(b) + 2
    return "\n\n".join(out)


def _facet_line(label: str, rows: list[dict], n: int = 6) -> str:
    if not rows:
        return ""
    return f"- {label}：" + "；".join(f"{r['name']}({r['count']})" for r in rows[:n])


def _portal_lines(portals: list[dict], n: int) -> list[str]:
    return [
        f"- {_clip(p.get('name'), 40)}"
        + (f" ［{p['verify']}］" if p.get("verify") and p["verify"] != "unverified" else "")
        + f" {p.get('url') or p.get('id')}"
        for p in portals[:n]
    ]


# ── search ──
def _search(data: dict, full: bool) -> tuple[dict, str]:
    results = data.get("results", [])
    total, returned, offset = data.get("total", 0), data.get("returned", 0), data.get("offset", 0)
    gaps = list(data.get("hints") or [])
    if not data.get("is_complete", True):
        gaps.append("部分实时来源失败，结果不完整（见 live.sources）")
    if any(r.get("missing_requirements") for r in results):
        gaps.append("部分结果有信息未知的筛选条件（discovery 模式保留并标注了缺什么）")
    nxt = []
    if total > offset + returned:
        nxt.append(f"还有 {total - offset - returned} 条：offset={offset + returned} 翻页")
    if any(r.get("delivery") == "generate" for r in results):
        nxt.append("delivery=可取数 的用 fetch（先预览，确认后 confirm=true）")
    if any(r.get("delivery") == "download" for r in results):
        nxt.append("delivery=可下载 的用 download")
    pres = {
        "operation_status": "complete" if data.get("is_complete", True) else "partial",
        "summary": f"“{data.get('query') or '（仅筛选）'}”命中 {total} 条，本次返回 {returned} 条。",
        "evidence": [
            f"{r['title']}（{r.get('why_matched') and r['why_matched'][0] or r['kind']}）"
            for r in results[:3]
        ],
        "gaps": gaps,
        "next_actions": nxt,
    }
    blocks = [f"{pres['summary']}（起点 {offset}，模式 {data.get('mode')}）"]
    blocks.append(
        "\n".join(_item(offset + i + 1, r, full=full) for i, r in enumerate(results))
        or "（没有命中）"
    )
    f = data.get("facets") or {}
    facet_lines = [
        x
        for x in (
            _facet_line("主题", f.get("topics", [])),
            _facet_line("地域", f.get("regions", [])),
            _facet_line("类型", f.get("kinds", [])),
            _facet_line("获取方式", f.get("delivery", [])),
        )
        if x
    ]
    if facet_lines:
        blocks.append("当前命中集的分面计数：\n" + "\n".join(facet_lines))
    if data.get("portals"):
        blocks.append(
            "相关门户（只有链接）：\n" + "\n".join(_portal_lines(data["portals"], 8 if full else 4))
        )
    if gaps:
        blocks.append("缺口：" + "；".join(gaps))
    if nxt:
        blocks.append("下一步：" + "；".join(nxt))
    return pres, _fit(blocks)


# ── plan ──
_PLAN_GROUPS = ("indicators", "indicator_links", "tables", "documents", "references")


def _plan(data: dict, full: bool) -> tuple[dict, str]:
    src = data["sources"]
    per = 8 if full else 4
    u = data.get("understanding", {})
    counts = {k: len(src[k]["items"]) for k in src}
    gen = sum(1 for i in src["indicators"]["items"] if i.get("delivery") == "generate")
    gaps = [c for c in data.get("caveats", [])]
    if not src["regional_portals"]["items"] and u.get("regions"):
        gaps.append("没有找到目标地区的官方入口")
    pres = {
        "operation_status": "complete",
        "summary": f"“{data['topic']}”：可取数指标 {counts['indicators']}（其中 {gen} 个可直接 fetch）、仅链接指标 {counts.get('indicator_links', 0)}、数据表 {counts['tables']}、公报 {counts['documents']}、说明文档 {counts['references']}。",
        "evidence": [
            f"理解：地区 {('、'.join(u.get('regions') or []) or '未指定')}；领域 {('、'.join(u.get('domains') or []) or '未识别')}；概念 {('、'.join(u.get('concepts') or []) or '无')}"
        ],
        "gaps": gaps,
        "next_actions": data.get("next_steps", [])[: (6 if full else 3)],
    }
    blocks = [pres["summary"], pres["evidence"][0]]
    for key in _PLAN_GROUPS:
        items = src[key]["items"]
        if items:
            body = "\n".join(_item(i + 1, it, full=full) for i, it in enumerate(items[:per]))
            more = (
                f'\n   …另有 {len(items) - per} 条（detail="full" 或 search 继续）'
                if len(items) > per
                else ""
            )
            blocks.append(f"### {src[key]['title']}\n{body}{more}")
    terms = src.get("glossary", {}).get("items", [])
    if terms:
        blocks.append(
            f"### {src['glossary']['title']}\n"
            + "\n".join(
                f"- **{t['name_zh']}**（{t['status_zh']}）：{_clip(t['definition'], 120 if not full else 220)} {t.get('source_url') or ''}"
                for t in terms[: (6 if full else 3)]
            )
        )
    for key in ("regional_portals", "official_portals"):
        items = src[key]["items"]
        if items:
            blocks.append(
                f"### {src[key]['title']}\n" + "\n".join(_portal_lines(items, 8 if full else 5))
            )
    entries = src["search_entries"]["items"]
    if entries:
        blocks.append(
            "### 继续检索的入口\n" + "\n".join(f"- {e['name']} {e['url']}" for e in entries[:6])
        )
    hints = data.get("analysis_hints", [])
    if hints:
        blocks.append(
            "### 统计分析建议\n"
            + "\n".join(
                f"- {_clip(h, 140 if not full else 400)}" for h in hints[: (6 if full else 3)]
            )
        )
    if gaps:
        blocks.append("### 缺口与提示\n" + "\n".join(f"- {g}" for g in gaps))
    blocks.append("### 下一步\n" + "\n".join(f"- {s}" for s in pres["next_actions"]))
    return pres, _fit(blocks)


# ── inspect ──
def _source_line(data: dict, r: dict) -> str:
    src = data.get("source") or {}
    portal = src.get("portal") or {}
    name = src.get("name") or r.get("source_id")
    return f"来源 {name}" + (f"（门户 {portal['home_url']}）" if portal.get("home_url") else "")


def _inspect(data: dict, full: bool) -> tuple[dict, str]:
    r = data["resource"]
    how = data.get("how_to_get") or {}
    path = " › ".join(p["title"] for p in data.get("parents", []))
    pres = {
        "operation_status": "complete",
        "summary": f"{r['title']}（{_tag(r)}），{data.get('child_count', 0)} 个子资源。",
        "evidence": [f"层级：{path or '（根）'}", f"可信度：{data.get('verification_level')}"],
        "gaps": [m for m in r.get("missing_requirements", [])],
        "next_actions": [
            f"{how['tool']}：{how['note']}" if how.get("tool") else how.get("note", "")
        ],
    }
    blocks = [
        pres["summary"],
        f"id: `{r['resource_id']}`\n层级：{path or '（根）'}\n"
        + " | ".join(x for x in (_where(r), _period(r), f"来源 {r.get('source_id')}") if x),
    ]
    d_lines = [
        f"- [{d['access_method']}→{_DELIVERY.get(d['delivery'], d['delivery'])}] {d.get('url')}"
        + (f"（{d['file_format']}）" if d.get("file_format") else "")
        + (f" ［{_USABILITY.get(d['usability'], d['usability'])}］" if d.get("usability") else "")
        + (f"：{_clip(d['note'], 80)}" if d.get("note") else "")
        for d in data.get("distributions", [])
    ]
    if d_lines:
        blocks.append("获取入口：\n" + "\n".join(d_lines[: (20 if full else 6)]))
    if how.get("needs"):
        blocks.append("取数还需要参数：" + "、".join(how["needs"]))
    if how.get("also"):
        blocks.append(f"另可 {how['also']['tool']}：{how['also']['note']}")
    kids = data.get("children")
    if kids:
        n = 50 if full else 12
        shown = "\n".join(_item(i + 1, c, full=False) for i, c in enumerate(kids[:n]))
        more = (
            f"\n（共 {data.get('child_count')} 个，这里列 {min(n, len(kids))} 个；用 limit/offset 翻页）"
            if data.get("child_count", 0) > min(n, len(kids))
            else ""
        )
        blocks.append("子资源：\n" + shown + more)
    for t in data.get("glossary", [])[:1]:
        blocks.append(
            f"术语说明（{t['status_zh']}）：{t['name_zh']}——{_clip(t['definition'], 160)} {t.get('source_url') or ''}"
        )
    blocks.append(f"获取方式：{how.get('tool') or '链接'} — {how.get('note', '')}")
    return pres, _fit(blocks)


# ── fetch / download ──
def _acquire(data: dict, full: bool) -> tuple[dict, str]:
    status = data.get("status")
    title = data.get("title") or data.get("resource_id")
    stage = f"阶段：{_STAGE[data['data_stage']]}" if data.get("data_stage") in _STAGE else ""
    if status == "plan":
        params = data.get("params") or {}
        what = (
            "取数参数："
            + json.dumps(
                {k: v for k, v in params.items() if v not in (None, [], "")}, ensure_ascii=False
            )
            if params
            else f"下载：{data.get('url')}（{data.get('file_format') or '?'}，{_USABILITY.get(data.get('usability') or '', '?')}）"
        )
        pres = {
            "operation_status": "needs_confirmation",
            "summary": f"已预览，未执行：{title}",
            "evidence": [
                stage,
                what,
                f"保存到 {data.get('save_to')}",
                f"许可：{data.get('license')}",
            ],
            "gaps": [w for w in [data.get("warning")] if w],
            "next_actions": [data.get("next_action") or "加 confirm=true 执行"],
        }
        blocks = [pres["summary"], "\n".join(f"- {e}" for e in pres["evidence"] if e)]
        if data.get("other_options"):
            blocks.append(
                "其他可选文件："
                + "；".join(
                    f"#{o['index']} {o.get('file_format')} {o['url']}"
                    for o in data["other_options"][:5]
                )
            )
        if pres["gaps"]:
            blocks.append("提示：" + "；".join(pres["gaps"]))
        blocks.append("下一步：" + pres["next_actions"][0])
        return pres, _fit(blocks)
    if status == "link":
        pres = {
            "operation_status": "partial",
            "summary": f"只有链接，无法自动获取：{data.get('landing_url')}",
            "evidence": [stage, *[x["url"] for x in data.get("links", [])[:5]]],
            "gaps": [data.get("note", "")],
            "next_actions": ["请用户在浏览器打开链接"],
        }
        return pres, _fit(
            [pres["summary"], "\n".join(f"- {u}" for u in pres["evidence"]), data.get("note", "")]
        )
    # done
    cov = data.get("coverage") or {}
    rows = data.get("row_count")
    gaps = []
    if cov.get("missing_cells"):
        gaps.append(
            f"覆盖缺口 {cov['missing_cells']}/{cov['expected_cells']} 个单元（缺口样例见 coverage.missing_sample）"
        )
    parse = (data.get("meta") or {}).get("解析")
    if isinstance(parse, dict) and parse.get("可信度") == "低":
        gaps.append("网页表格解析可信度低，请对照原表核对")
    partial = bool(gaps)
    pres = {
        "operation_status": "partial" if partial else "complete",
        "summary": f"已取回并保存：{title}" + (f"，{rows} 行" if rows is not None else ""),
        "evidence": [stage, f"文件：{data.get('saved_to')}"]
        + (
            [
                f"覆盖：{cov.get('observed_cells')}/{cov.get('expected_cells')}（{cov.get('status')}）"
            ]
            if cov
            else []
        )
        + ([f"体积 {data.get('size_bytes')} 字节"] if data.get("size_bytes") else []),
        "gaps": gaps,
        "next_actions": ["describe 对导出文件重算描述统计；引用时写明来源与取数时间"],
    }
    blocks = [pres["summary"], "\n".join(f"- {e}" for e in pres["evidence"])]
    cols = data.get("columns")
    if cols and data.get("rows"):
        n = 20 if full else 6
        head = "| " + " | ".join(map(str, cols)) + " |\n|" + "---|" * len(cols)
        body = "\n".join(
            "| " + " | ".join(_clip(c, 24) for c in row) + " |" for row in data["rows"][:n]
        )
        blocks.append(
            f"前 {min(n, len(data['rows']))} 行预览（共 {rows} 行，完整在文件里）：\n{head}\n{body}"
        )
    if data.get("describe_markdown"):
        blocks.append(
            "描述统计：\n"
            + (data["describe_markdown"] if full else _clip_lines(data["describe_markdown"], 14))
        )
    if gaps:
        blocks.append("缺口：" + "；".join(gaps))
    return pres, _fit(blocks)


def _clip_lines(text: str, n: int) -> str:
    lines = text.splitlines()
    return "\n".join(lines[:n]) + (
        f'\n…（另有 {len(lines) - n} 行，detail="full" 或 describe 查看）' if len(lines) > n else ""
    )


# ── 其他工具 ──
def _facets(data: dict, full: bool) -> tuple[dict, str]:
    topics = data.get("topics", [])
    l1 = [t for t in topics if t.get("level") == 1]
    lines = []
    for t in l1:
        subs = [s for s in topics if s.get("parent_id") == t["id"]]
        lines.append(
            f"- {t['name']} `{t['id']}`({t['count']})"
            + (("：" + "、".join(f"{s['name']}({s['count']})" for s in subs)) if subs else "")
        )
    regs = "；".join(
        f"{r['name']}`{r['id']}`({r['count']})"
        for r in data.get("regions", [])[: (40 if full else 12)]
    )
    pres = {
        "operation_status": "complete",
        "summary": f"{len(topics)} 个主题、{len(data.get('regions', []))} 个有资源的地域、{len(data.get('kinds', []))} 类资源。",
        "evidence": [],
        "gaps": [],
        "next_actions": ["把 id 作为 search 的 topic_ids / region_ids / kinds / delivery"],
    }
    blocks = [
        pres["summary"],
        "主题（id 与资源数）：\n" + "\n".join(lines),
        "地域（资源数最多的）：" + regs,
    ]
    blocks.append(
        "类型：" + "；".join(f"{k['name']}`{k['id']}`({k['count']})" for k in data.get("kinds", []))
    )
    blocks.append(
        "获取方式："
        + "；".join(f"{k['name']}`{k['id']}`({k['count']})" for k in data.get("delivery", []))
    )
    blocks.append(
        "地域粒度："
        + " / ".join(data.get("region_levels", []))
        + "；模式："
        + "；".join(f"{k}={v}" for k, v in data.get("modes", {}).items())
    )
    return pres, _fit(blocks)


def _sources(data: dict, full: bool) -> tuple[dict, str]:
    rows = data.get("catalog_sources", [])
    by_adapter: dict[str, list[dict]] = {}
    for r in rows:
        by_adapter.setdefault(r.get("adapter") or "?", []).append(r)
    lines = []
    for ad, items in by_adapter.items():
        items.sort(key=lambda x: -x.get("resources", 0))
        top = items if full else items[:6]
        lines.append(
            f"- 适配器 {ad}（{len(items)} 个来源，{sum(i.get('resources', 0) for i in items)} 条）："
            + "；".join(f"{i['name']}`{i['source_id']}`({i.get('resources', 0)})" for i in top)
            + ("…" if len(top) < len(items) else "")
        )
    live = data.get("live_sources", [])
    pres = {
        "operation_status": "complete",
        "summary": f"目录来源 {len(rows)} 个，实时检索来源 {len(live)} 个，门户库 {data.get('portals')} 条。",
        "evidence": [],
        "gaps": [data["note"]] if data.get("note") else [],
        "next_actions": ["search 用 source_ids / domain_ids 限定来源"],
    }
    blocks = [
        pres["summary"],
        "\n".join(lines),
        "实时来源：" + "；".join(f"{s['title']}`{s['id']}`({s['status']})" for s in live),
    ]
    if data.get("note"):
        blocks.append(data["note"])
    return pres, _fit(blocks)


def _status(data: dict, full: bool) -> tuple[dict, str]:
    cat = data.get("catalog") or {}
    kinds_ = "；".join(
        f"{k} {n}" for k, n in sorted((cat.get("by_kind") or {}).items(), key=lambda x: -x[1])
    )
    gaps = list(data.get("hints") or [])
    pres = {
        "operation_status": "partial" if gaps else "complete",
        "summary": f"目录 {cat.get('resources', 0)} 条资源（{len(cat.get('sources') or {})} 个来源），门户库 {data.get('portals', 0)} 条。",
        "evidence": [
            f"目录最近更新：{cat.get('last_indexed')}",
            f"门户库同步：{data.get('portals_synced_at')}",
        ],
        "gaps": gaps,
        "next_actions": gaps[:3],
    }
    blocks = [
        pres["summary"],
        "类型分布：" + kinds_,
        f"版本 {data.get('version')}；导出目录 {data.get('exports_dir')}",
    ]
    blocks.append("\n".join(f"- {e}" for e in pres["evidence"]))
    if gaps:
        blocks.append("需要处理：" + "；".join(gaps))
    return pres, _fit(blocks)


def _generic(data: dict, full: bool) -> tuple[dict, str]:
    pres = {
        "operation_status": "complete",
        "summary": "",
        "evidence": [],
        "gaps": [],
        "next_actions": [],
    }
    return pres, json.dumps(data, ensure_ascii=False, separators=(",", ":"), default=str)


def _describe(data: dict, full: bool) -> tuple[dict, str]:
    pres = {
        "operation_status": "complete",
        "summary": "已重新计算描述统计",
        "evidence": [],
        "gaps": [],
        "next_actions": [],
    }
    return pres, data.get("markdown") or json.dumps(
        data, ensure_ascii=False, separators=(",", ":"), default=str
    )


RENDERERS = {
    "search": _search,
    "plan": _plan,
    "inspect": _inspect,
    "fetch": _acquire,
    "download": _acquire,
    "facets": _facets,
    "sources": _sources,
    "status": _status,
    "describe": _describe,
}


def present(
    tool: str, data: dict[str, Any], *, detail: str = "concise"
) -> tuple[dict[str, Any], str]:
    """返回 (带 presentation 的完整数据, 简明文字)。完整数据除多了 `presentation` 外不变。"""
    mode = normalize_detail(detail)
    pres, text = RENDERERS.get(tool, _generic)(data, mode == "full")
    pres["evidence"] = [e for e in pres["evidence"] if e]
    out = {**data, "presentation": pres}
    if mode == "json":
        text = json.dumps(out, ensure_ascii=False, separators=(",", ":"), default=str)
    return out, text


__all__ = ["DETAILS", "RENDERERS", "normalize_detail", "present"]
