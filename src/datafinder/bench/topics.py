"""真实选题基准：给定研究主题，检查"找数方案"（plan_sources）是否给出了该有的来源。

与排序基准（ranking）的区别：这里不比较排序方案，而是用真实选题做**验收**——
每个选题写明"应该能找到什么"（门户、国家统计局指标、年鉴数据表），逐条检查是否命中。
结果取决于本机已建立的门户库与资源目录；目录是空的会直接报错并提示怎么建立，不会悄悄给出 0 分。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import typer
import yaml
from rich.console import Console
from rich.table import Table

from ..service import ServiceError, plan_sources

console = Console()
topics_app = typer.Typer(no_args_is_help=True)

SOURCE_ZH = {"public": "公开题", "adapted": "改写自公开题型", "authored": "自拟补充"}
KINDS = ("portals", "indicators", "tables", "documents")
KIND_ZH = {"portals": "门户", "indicators": "指标", "tables": "数据表", "documents": "公报/发布"}


@dataclass
class TopicCase:
    id: str
    topic: str
    region: str | None = None
    expect: dict[str, list[list[str]]] = field(
        default_factory=dict
    )  # kind -> [任一关键词命中即算该条通过]
    source: str | None = None  # public 公开题 / adapted 改写 / authored 自拟（公开题集才有）


def load_topics(directory: Path, name: str = "topics.yaml") -> list[TopicCase]:
    raw = yaml.safe_load((directory / name).read_text(encoding="utf-8"))
    cases = []
    for t in raw["topics"]:
        expect = {}
        for kind in KINDS:
            groups = t.get("expect", {}).get(kind, [])
            expect[kind] = [[g] if isinstance(g, str) else list(g) for g in groups]
        cases.append(TopicCase(t["id"], t["topic"], t.get("region"), expect, t.get("source")))
    return cases


def _texts(plan: dict[str, Any]) -> dict[str, list[str]]:
    src = plan["sources"]
    return {
        "portals": [
            f"{p['name']} {p.get('name_en') or ''}"
            for g in ("regional_portals", "official_portals")
            for p in src[g]["items"]
        ],
        "indicators": [f"{i['title']} {i.get('path') or ''}" for i in src["indicators"]["items"]],
        "tables": [f"{t['title']} {t.get('path') or ''}" for t in src["tables"]["items"]],
        "documents": [f"{d['title']} {d.get('path') or ''}" for d in src["documents"]["items"]],
    }


def evaluate(case: TopicCase, plan: dict[str, Any]) -> dict[str, Any]:
    """纯函数：把找数方案的结果与期望逐条比对。"""
    texts = _texts(plan)
    out: dict[str, Any] = {
        "id": case.id,
        "topic": case.topic,
        "source": case.source,
        "kinds": {},
    }
    for kind in KINDS:
        groups = case.expect.get(kind, [])
        if not groups:
            continue
        misses = [
            "/".join(g) for g in groups if not any(any(k in t for k in g) for t in texts[kind])
        ]
        out["kinds"][kind] = {
            "total": len(groups),
            "hit": len(groups) - len(misses),
            "misses": misses,
        }
    return out


def recall(results: list[dict[str, Any]]) -> tuple[int, int]:
    hit = total = 0
    for r in results:
        for k in r["kinds"].values():
            hit += k["hit"]
            total += k["total"]
    return hit, total


@topics_app.command("topics")
def topics(
    directory: Path = typer.Option(Path("bench"), "--dir", help="基准目录（含 topics.yaml）"),
    holdout: bool = typer.Option(
        False, "--holdout", help="跑留出集 topics.holdout.yaml（调参时没看过的选题）"
    ),
    public: bool = typer.Option(
        False, "--public", help="跑公开来源选题集 topics.public.yaml（评测集/赛事/真实用户问题）"
    ),
    only: str | None = typer.Option(None, "--only", help="只跑这个选题 id"),
    min_recall: float = typer.Option(
        0.0, "--min-recall", min=0, max=1, help="总命中率低于此值时退出码为 1"
    ),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """用真实选题验收找数方案（只读本地门户库、指标索引与年鉴目录，不联网）。"""
    name = "topics.public.yaml" if public else "topics.holdout.yaml" if holdout else "topics.yaml"
    cases = [c for c in load_topics(directory, name) if not only or c.id == only]
    if not cases:
        raise typer.BadParameter("没有匹配的选题")
    results = []
    for c in cases:
        try:
            plan = plan_sources(c.topic, c.region)
        except ServiceError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(code=1) from exc
        results.append(evaluate(c, plan))
    hit, total = recall(results)
    if as_json:
        typer.echo(
            json.dumps(
                {"hit": hit, "total": total, "results": results}, ensure_ascii=False, indent=2
            )
        )
    else:
        t = Table(show_header=True, header_style="bold", box=None, pad_edge=False)
        t.add_column("选题")
        for kind in KINDS:
            t.add_column(KIND_ZH[kind], justify="right")
        for r in results:
            cells = []
            for kind in KINDS:
                k = r["kinds"].get(kind)
                cells.append("—" if not k else f"{k['hit']}/{k['total']}")
            t.add_row(f"{r['id']}  [dim]{r['topic']}[/dim]", *cells)
        console.print(t)
        for r in results:
            for kind, k in r["kinds"].items():
                for m in k["misses"]:
                    console.print(f"  [yellow]未命中[/yellow] {r['id']} · {KIND_ZH[kind]}：{m}")
        rate = hit / total if total else 0.0
        console.print(f"\n总命中 {hit}/{total}（{rate:.0%}）。")
        by_source: dict[str, list[dict[str, Any]]] = {}
        for r in results:
            if r["source"]:
                by_source.setdefault(r["source"], []).append(r)
        for src, rs in by_source.items():
            h, n = recall(rs)
            console.print(f"  [dim]{SOURCE_ZH.get(src, src)}：{h}/{n}（{h / n:.0%}）[/dim]")
    if total and hit / total < min_recall:
        raise typer.Exit(code=1)
