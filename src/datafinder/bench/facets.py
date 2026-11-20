"""分面检索验收：用一批真实查询 + 筛选条件，检查统一目录检索是否给出该有的、不给误导的。

与选题验收（topics）的区别：这里直接验收**三分面 × 获取方式 × 观测期**的筛选语义，特别是"必须不出现"的误导项：
出版年冒充观测期、发布机构所在地冒充数据覆盖地域、标题推断的地域粒度冒充已验证、图片冒充可分析表格……
每个用例写明：筛选条件、至少要命中什么（hit）、所有结果必须满足什么（all）、绝不能出现什么（forbid）。
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import typer
import yaml
from rich.console import Console
from rich.table import Table

from .. import service

console = Console()
facets_app = typer.Typer(no_args_is_help=True)


@dataclass
class FacetCase:
    id: str
    query: str = ""
    filters: dict[str, Any] = field(default_factory=dict)
    expect: dict[str, Any] = field(default_factory=dict)
    note: str = ""


def load_cases(directory: Path, name: str = "facets.yaml") -> list[FacetCase]:
    raw = yaml.safe_load((directory / name).read_text(encoding="utf-8"))
    return [
        FacetCase(
            c["id"],
            c.get("query", ""),
            c.get("filters", {}),
            c.get("expect", {}),
            c.get("note", ""),
        )
        for c in raw["cases"]
    ]


def _as_list(v: Any) -> list:
    return v if isinstance(v, list) else [v]


def matches(item: dict[str, Any], m: dict[str, Any]) -> bool:
    """一个结果是否满足一组条件（条件之间 AND；同一条件的多个取值 OR）。"""
    for key, want in m.items():
        wants = _as_list(want)
        if key == "title_any":
            ok = any(w in item["title"] for w in wants)
        elif key == "topic":
            ok = any(w in item["topic_ids"] for w in wants)
        elif key == "region":
            ok = any(w == r["region_id"] for w in wants for r in item["regions"])
        elif key == "path_prefix":
            ok = any((item.get("path") or "").startswith(w) for w in wants)
        elif key == "source_prefix":
            ok = any(item["source_id"].startswith(w) for w in wants)
        elif key == "period_known":
            ok = (item["period"]["start"] is not None) == bool(want)
        elif key == "missing_any":
            ok = any(any(w in x for w in wants) for x in item["missing_requirements"])
        elif key == "versions_min":
            ok = len(item.get("versions") or []) >= want
        else:  # kind / delivery / usability / source_id / verification_level …：取值相等
            ok = item.get(key) in wants
        if not ok:
            return False
    return True


def evaluate(case: FacetCase, response: dict[str, Any]) -> list[str]:
    """返回失败原因列表（空 = 通过）。纯函数，便于单元测试。"""
    results, e, fails = response["results"], case.expect, []
    lo, hi = e.get("min_results", 0 if "max_results" in e else 1), e.get("max_results")
    if len(results) < lo:
        fails.append(f"结果太少：{len(results)} < {lo}")
    if hi is not None and len(results) > hi:
        fails.append(f"结果太多：{len(results)} > {hi}（{[r['title'] for r in results[:3]]}…）")
    for m in e.get("hit", []):
        if not any(matches(r, m) for r in results):
            fails.append(f"应命中而未命中：{m}")
    for m in _as_list(e["all"]) if "all" in e else []:
        bad = [r["title"] for r in results if not matches(r, m)]
        if bad:
            fails.append(f"有结果不满足 {m}：{bad[:2]}")
    for m in e.get("forbid", []):
        bad = [r["title"] for r in results if matches(r, m)]
        if bad:
            fails.append(f"出现了不该出现的 {m}：{bad[:2]}")
    if e.get("hint_contains") and not any(
        e["hint_contains"] in h for h in response.get("hints", [])
    ):
        fails.append(f"提示里应包含「{e['hint_contains']}」")
    return fails


def run_case(case: FacetCase) -> tuple[list[str], dict[str, Any]]:
    f = dict(case.filters)
    kw = {
        "topic_ids": f.get("topic"),
        "region_ids": f.get("region"),
        "kinds_": f.get("kind"),
        "delivery": f.get("delivery"),
        "geo_granularity": f.get("granularity"),
        "period_start": f.get("from"),
        "period_end": f.get("to"),
        "frequency": f.get("frequency"),
        "mode": f.get("mode", "discovery"),
        "region_match": f.get("region_match", "intersects"),
        "limit": f.get("limit", 20),
        "facets": False,
    }
    resp = asyncio.run(service.search(case.query, **{k: v for k, v in kw.items() if v is not None}))
    return evaluate(case, resp), resp


@facets_app.command("facets")
def facets_cmd(
    directory: Path = typer.Option(Path("bench"), "--dir", help="基准目录（含 facets.yaml）"),
    holdout: bool = typer.Option(False, "--holdout", help="跑留出集 facets.holdout.yaml"),
    only: str | None = typer.Option(None, "--only", help="只跑这个用例 id"),
    min_pass: float = typer.Option(
        0.0, "--min-pass", min=0, max=1, help="通过率低于此值时退出码为 1"
    ),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """用真实查询验收分面检索（只读本地资源目录，不联网）。"""
    cases = [
        c
        for c in load_cases(directory, "facets.holdout.yaml" if holdout else "facets.yaml")
        if not only or c.id == only
    ]
    if not cases:
        raise typer.BadParameter("没有匹配的用例")
    rows = []
    for c in cases:
        try:
            fails, resp = run_case(c)
        except service.ServiceError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(code=1) from exc
        rows.append(
            {
                "id": c.id,
                "query": c.query,
                "filters": c.filters,
                "pass": not fails,
                "fails": fails,
                "returned": len(resp["results"]),
            }
        )
    passed = sum(r["pass"] for r in rows)
    if as_json:
        typer.echo(
            json.dumps(
                {"passed": passed, "total": len(rows), "cases": rows}, ensure_ascii=False, indent=2
            )
        )
    else:
        t = Table(show_header=True, header_style="bold", box=None, pad_edge=False)
        for col in ("用例", "查询 / 筛选", "结果数", "通过"):
            t.add_column(col)
        for r in rows:
            t.add_row(
                r["id"],
                f"{r['query']} {json.dumps(r['filters'], ensure_ascii=False) if r['filters'] else ''}",
                str(r["returned"]),
                "[green]✓[/green]" if r["pass"] else "[red]✗[/red]",
            )
        console.print(t)
        for r in rows:
            for f in r["fails"]:
                console.print(f"  [yellow]失败[/yellow] {r['id']}：{f}")
        console.print(f"\n通过 {passed}/{len(rows)}（{passed / len(rows):.0%}）。")
    if passed / len(rows) < min_pass:
        raise typer.Exit(code=1)
