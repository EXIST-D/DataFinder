"""读取术语词典数据：glossary_extract 抽取的官方原文（按主题分文件）+ curated.yaml 的人工补充。

人工补充只改别名、说明与关系，不改官方原文的定义。关系的对端用术语名引用（同名时写 "页面key/术语名"）。
"""

from __future__ import annotations

from typing import Any

from datafinder.resources import load_package_yaml

from . import DOMAIN_ID

PACKAGE = f"datafinder.domains.{DOMAIN_ID}"


def _read(*parts: str) -> Any:
    try:
        return load_package_yaml(PACKAGE, "glossary", *parts)
    except FileNotFoundError:
        return None


def _resolve(index: dict[str, list[dict]], ref: str) -> dict:
    page, _, name = ref.rpartition("/")
    cands = index.get(name, [])
    if page:
        cands = [t for t in cands if t["term_id"].startswith(f"cnstat.term.{page}.")]
    if len(cands) != 1:
        raise ValueError(
            f"curated.yaml 里的引用 {ref!r} "
            + ("找不到术语" if not cands else f"有 {len(cands)} 个同名术语，请写成 页面key/术语名")
        )
    return cands[0]


def load() -> tuple[list[dict[str, Any]], dict[str, list[str]]]:
    """返回 (术语列表, 可回流检索的别名 → [规范名, 别名])。

    回流检索的只有官方原文里明确写出的缩写/又称（抽取时得到的 aliases）；人工补充的别名只用于查词与展示。
    """
    pages = load_package_yaml(PACKAGE, "glossary", "pages.yaml")["pages"]
    terms: list[dict[str, Any]] = []
    for f in dict.fromkeys(p["file"] for p in pages):
        data = _read(f"{f}.yaml")
        if data:
            terms += data["terms"]
    search_aliases = {
        a: [t["name_zh"], a]
        for t in terms
        if t.get("verification_status") in ("official_sourced", "reviewed")
        for a in t.get("aliases", [])
    }
    index: dict[str, list[dict]] = {}
    for t in terms:
        index.setdefault(t["name_zh"], []).append(t)
    for patch in (_read("curated.yaml") or {}).get("patches", []):
        t = _resolve(index, patch["ref"])
        t["aliases"] = list(dict.fromkeys([*t.get("aliases", []), *patch.get("aliases", [])]))
        if patch.get("note"):
            t["note"] = patch["note"]
        for rel in patch.get("relations", []):
            target = _resolve(index, rel["target"])
            t.setdefault("relations", []).append(
                {"type": rel["type"], "target_id": target["term_id"], "note": rel.get("note")}
            )
    return terms, search_aliases
