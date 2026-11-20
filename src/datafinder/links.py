"""外部检索入口：把当前关键词填进各检索引擎的搜索链接。清单见 data/links.yaml。"""

from __future__ import annotations

from urllib.parse import quote

from .query.plan import parse
from .resources import load_yaml

CHECK_NOTE = {
    "ok": None,
    "bot": "该站对自动访问返回验证页（真人浏览器正常），链接格式按官方搜索路径，未能自动核验",
    "nokey": "该站没有关键词参数，只会打开站点本身，请在站内搜索框输入",
}


def build_links(q: str = "") -> list[dict]:
    """返回按分组整理的检索入口。`q` 为空时模板里的 {q} 替换为空串（只做导航）。"""
    cfg = load_yaml("links.yaml")
    q = q.strip()
    plan = parse(q) if q else None
    en = (plan.query_en() if plan else "") or q
    groups = [{"id": g["id"], "zh": g["zh"], "links": []} for g in cfg["groups"]]
    by_id = {g["id"]: g for g in groups}
    for item in cfg["links"]:
        used = en if item["lang"] == "en" else q
        by_id[item["group"]]["links"].append(
            {
                "id": item["id"],
                "name": item["name"],
                "desc": item.get("desc"),
                "scope": item.get("scope"),
                "check": item["check"],
                "note": CHECK_NOTE.get(item["check"]),
                "url": item["url"].replace("{q}", quote(used, safe="")),
                "query": used if "{q}" in item["url"] else None,
            }
        )
    return [g for g in groups if g["links"]]
