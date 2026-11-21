"""从国家统计局"指标解释"页面抽取术语定义（官方原文，逐字，不改写）。

这是数据维护工具，不在运行时使用：

    python -m datafinder.domains.official_statistics_cn.glossary_extract          # 联网抓取（每页间隔 2 秒）
    python -m datafinder.domains.official_statistics_cn.glossary_extract --cache DIR   # 用已保存的网页

页面结构：正文在第一个 `TRS_PreAppend`（或 `trs_editor_view`）容器里（页面里还有一份手机版重复内容，不取）；
每个术语是一个段落："（全角空格×2）术语名（全角空格）定义……"，其后没有"术语名＋全角空格"开头的段落是上一术语的续段。
抽取只做结构还原：术语名取自全角空格之前，定义取其后原文；识别不了的段落不丢弃，作为续段并入上一术语。
每个术语写明出处页面与抓取日期，状态为 official_sourced（官方原文），不是 reviewed（人工审校）。
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import re
from collections import defaultdict
from datetime import date
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import yaml

from datafinder.resources import load_package_yaml

from . import DOMAIN_ID

PACKAGE = f"datafinder.domains.{DOMAIN_ID}"
DATA = Path(__file__).parent / "data" / "glossary"
IDEO = "\u3000"  # 全角空格
_NAME_BAD = re.compile(r"[，。；：！？、,;:]")  # 术语名里不会有句读（顿号除外的情形见下）
_ABBR = re.compile(r"^(.*?)[（(]\s*([A-Za-z][A-Za-z0-9/&.\- ]{0,19})\s*[）)]$")
_AKA = re.compile(r"^(.*?)[（(]又称([^（）()]+)[）)]$")  # 出生率（又称粗出生率）
_ZERO = re.compile("[\u200b\ufeff]")
_ENUM = re.compile(
    r"^[（(]?\d+[）).．、]"
)  # （1）中央 / 2.基金收入：上一个术语里的分项，不是独立术语
_CONTAINER = re.compile(r"TRS_PreAppend|TRS_Editor|trs_editor_view")  # 正文容器（两种页面模板）


class _Article(HTMLParser):
    """取第一个 TRS_PreAppend 容器里的 <p> 段落文字（保留全角空格）。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.paras: list[str] = []
        self._depth = 0  # 进入容器后的 div 深度；0 = 还没进入或已结束
        self._started = False
        self._done = False
        self._buf: list[str] | None = None
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self._done:
            return
        if tag in ("script", "style"):
            self._skip += 1
        if tag == "div":
            cls = dict(attrs).get("class") or ""
            if not self._started and _CONTAINER.search(cls):
                self._started, self._depth = True, 1
            elif self._started:
                self._depth += 1
        elif tag == "p" and self._started:
            self._buf = []

    def handle_endtag(self, tag: str) -> None:
        if self._done:
            return
        if tag in ("script", "style"):
            self._skip = max(0, self._skip - 1)
        if tag == "p" and self._buf is not None:
            self.paras.append("".join(self._buf).replace("\xa0", " "))
            self._buf = None
        elif tag == "div" and self._started:
            self._depth -= 1
            if self._depth == 0:
                self._done = True

    def handle_data(self, data: str) -> None:
        if self._buf is not None and not self._skip:
            self._buf.append(data)


def _split_name(name: str) -> tuple[str, list[str]]:
    """ "国内生产总值（GDP）" → ("国内生产总值", ["GDP"])；"出生率（又称粗出生率）" → ("出生率", ["粗出生率"])；其余原样。"""
    for pattern in (_ABBR, _AKA):
        m = pattern.match(name)
        if m and m.group(1).strip():
            return m.group(1).strip(), [m.group(2).strip()]
    return name, []


def extract_terms(html: str) -> list[dict[str, Any]]:
    """页面 → [{name_zh, aliases, definition}]（定义为官方原文，续段用换行连接）。同名术语只留第一次出现。"""
    p = _Article()
    p.feed(html)
    terms: list[dict[str, Any]] = []
    for raw in p.paras:
        body = _ZERO.sub("", raw).strip()  # strip() 去掉所有空白，含全角空格与 U+2002
        if not body:
            continue
        head, sep, rest = body.partition(IDEO)
        if not (sep and rest) and raw.lstrip(" ").startswith(
            IDEO
        ):  # 另一种模板：缩进用全角空格，名称与定义间是半角空格
            head, sep, rest = body.partition(" ")
        head, rest = head.strip(), rest.strip()
        if (
            sep
            and rest
            and 0 < len(head) <= 40
            and not _NAME_BAD.search(head)
            and not _ENUM.match(head)
        ):
            name, aliases = _split_name(re.sub(r"\s+", "", head))
            terms.append({"name_zh": name, "aliases": aliases, "definition": rest})
        elif terms:
            terms[-1]["definition"] += "\n" + re.sub(r"\s+", " ", body)
    seen: set[str] = set()
    unique = []
    for t in terms:
        if t["name_zh"] not in seen:
            seen.add(t["name_zh"])
            unique.append(t)
    return unique


def term_id(page_key: str, name: str) -> str:
    """稳定 id：页面主题 + 术语名的短哈希（改定义不改 id；术语名改了就是新术语）。"""
    return f"cnstat.term.{page_key}.{hashlib.sha1(name.encode()).hexdigest()[:8]}"


def build(
    pages: list[dict[str, Any]], htmls: dict[str, str], retrieved: str
) -> dict[str, list[dict[str, Any]]]:
    """pages（pages.yaml）+ 各页 HTML → {输出文件名: 术语列表}。"""
    out: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for page in pages:
        html = htmls.get(page["key"])
        if not html:
            continue
        for t in extract_terms(html):
            note = (
                "官方页面里的计算公式是图片，这里没有收录；请到出处页面查看。"
                if "计算公式" in t["definition"]
                else None
            )
            out[page["file"]].append(
                {
                    "term_id": term_id(page["key"], t["name_zh"]),
                    "name_zh": t["name_zh"],
                    **({"aliases": t["aliases"]} if t["aliases"] else {}),
                    "topic_ids": list(page["topic_ids"]),
                    "definition": t["definition"],
                    **({"scope_note": note} if note else {}),
                    "source_name": f"国家统计局·指标解释·{page['title']}",
                    "source_url": page["url"],
                    "retrieved_at": retrieved,
                    "verification_status": "official_sourced",
                }
            )
    return out


def write(files: dict[str, list[dict[str, Any]]], directory: Path = DATA) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for name, terms in files.items():
        text = yaml.safe_dump(
            {"version": 1, "terms": terms}, allow_unicode=True, sort_keys=False, width=100000
        )
        (directory / f"{name}.yaml").write_text(
            "# 由 glossary_extract 从国家统计局指标解释页面抽取（官方原文）；人工补充请写在 curated.yaml，不要改这里。\n"
            + text,
            encoding="utf-8",
        )


async def _fetch(pages: list[dict[str, Any]]) -> dict[str, str]:
    from datafinder.engine.http import Http

    http = Http(default_interval=2.0)  # 国家统计局：串行、2 秒间隔，遇验证页即停（SourceError）
    out: dict[str, str] = {}
    try:
        for page in pages:
            for attempt in range(
                3
            ):  # 该站偶尔对正常页面间歇性返回 404：隔几秒再试，最多两次（遇验证页仍会立即中止）
                status, data = await http.get_bytes(page["url"])
                if status == 200:
                    out[page["key"]] = data.decode("utf-8", "ignore")
                    break
                if attempt < 2:
                    await asyncio.sleep(6)
            else:
                print(f"跳过 {page['title']}：HTTP {status}")
    finally:
        await http.aclose()
    return out


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--cache", type=Path, help="目录里放 <页面key>.html，不联网")
    args = ap.parse_args(argv)
    pages = load_package_yaml(PACKAGE, "glossary", "pages.yaml")["pages"]
    if args.cache:
        htmls = {
            p["key"]: f.read_bytes().decode("utf-8", "ignore")
            for p in pages
            if (f := args.cache / f"{p['key']}.html").exists()
        }
    else:
        htmls = asyncio.run(_fetch(pages))
    files = build(pages, htmls, date.today().isoformat())
    write(files)
    for name, terms in files.items():
        print(f"{name}.yaml：{len(terms)} 条")


if __name__ == "__main__":
    main()
