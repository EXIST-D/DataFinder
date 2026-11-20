"""命令行：统计年鉴目录（建立、查找、覆盖情况）。"""

from __future__ import annotations

import asyncio
import json

import typer
from rich.console import Console
from rich.table import Table as RichTable

from .. import service
from ..domains.official_statistics_cn.yearbook import crawl as yb_crawl
from ..domains.official_statistics_cn.yearbook import index as yb_index
from ..domains.official_statistics_cn.yearbook.registry import load_series
from ..engine.http import Http
from ..service import ServiceError

console = Console()
yearbook_app = typer.Typer(help="统计年鉴目录：按年鉴 × 年份建立数据表清单，并按关键词查找。", no_args_is_help=True)


def _hosts(series_list) -> dict[str, float]:
    from urllib.parse import urlsplit

    return {urlsplit(s.base.format(year=2024)).netloc: 1.0 for s in series_list if s.base}


@yearbook_app.command("index")
def index_cmd(
    series: str = typer.Option("", "--series", "-s", help="只抓这些系列，逗号分隔（默认全部可抓取的），见 yearbook list"),
    year_from: int | None = typer.Option(None, "--from", help="起始年份"),
    year_to: int | None = typer.Option(None, "--to", help="结束年份"),
    refresh: bool = typer.Option(False, "--refresh", help="重抓已有版次"),
) -> None:
    """建立年鉴目录（一版次一个请求，串行、间隔 ≥1 秒；已完成的版次会跳过）。"""
    wanted = {x.strip() for x in series.split(",") if x.strip()}
    chosen = [s for s in load_series() if s.crawl and (not wanted or s.id in wanted)]
    unknown = wanted - {s.id for s in load_series()}
    if unknown:
        raise typer.BadParameter(f"未知系列：{', '.join(sorted(unknown))}（见 datafinder yearbook list）")
    if not chosen:
        raise typer.BadParameter("没有可抓取的系列")
    console.print(f"将抓取 {len(chosen)} 个年鉴系列的目录页：串行、每次间隔 ≥1 秒，只保存表号/标题/链接，不下载表格文件。\n")

    async def go() -> yb_crawl.CrawlResult:
        http = Http(min_interval=_hosts(chosen))
        conn = yb_index.connect_index()
        try:
            return await yb_crawl.build(
                http, conn, chosen, years=(year_from, year_to), refresh=refresh,
                progress=lambda m: console.print(f"  [dim]{m}[/dim]"),
            )
        finally:
            conn.close()
            await http.aclose()

    try:
        res = asyncio.run(go())
    except KeyboardInterrupt:
        console.print("\n已中断，进度已保存，再运行同一命令即可续跑。")
        raise typer.Exit(code=130) from None
    console.print(
        f"\n[green]完成[/green]：新增 {len(res.ok)} 个版次（{sum(n for _, _, n in res.ok)} 张表），"
        f"{len(res.missing)} 个版次不存在，{len(res.failed)} 个失败。"
    )
    for line in res.stopped:
        console.print(f"[yellow]已放弃（站点拒绝或连不上，不会重试或绕过）：{line}[/yellow]")
    if res.stopped:
        console.print("[dim]这些系列只能用浏览器打开；换个网络环境后重跑同一命令可再试。[/dim]")


@yearbook_app.command("list")
def list_cmd(as_json: bool = typer.Option(False, "--json")) -> None:
    """各年鉴系列的覆盖情况。"""
    data = service.yearbook_catalog()
    if as_json:
        typer.echo(json.dumps(data, ensure_ascii=False, indent=2))
        return
    t = RichTable(show_header=True, header_style="bold", box=None, pad_edge=False)
    for col in ("系列", "名称", "已建版次", "表数", "状态"):
        t.add_column(col)
    for s in data["series"]:
        ys = s["years_ok"]
        span = f"{ys[0]}–{ys[-1]}（{len(ys)} 版）" if ys else "—"
        status = "可抓取" if s["crawlable"] else "仅导航"
        if s["years_failed"]:
            status += f"，{len(s['years_failed'])} 个版次失败"
        t.add_row(s["series"], s["name"], span, str(s["tables"]), status)
    console.print(t)


@yearbook_app.command("find")
def find_cmd(
    query: str = typer.Argument(..., help="如 “规模以上工业企业主要指标”"),
    region: str | None = typer.Option(None, "--region", "-r", help="地区码，如 CN-HE（默认从问句里识别）"),
    year: int | None = typer.Option(None, "--year", "-y", help="只看这一版次"),
    series: str | None = typer.Option(None, "--series", "-s"),
    fmt: str | None = typer.Option(None, "--fmt", help="xls / html / jpg / pdf …"),
    limit: int = typer.Option(15, "--limit", "-n"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """按关键词找年鉴里的数据表，并列出有哪些年份可查。"""
    try:
        res = service.yearbook_find(query, region=region, year=year, series=series, fmt=fmt, limit=limit)
    except ServiceError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc
    if as_json:
        typer.echo(json.dumps(res, ensure_ascii=False, indent=2))
        return
    for h in res["tables"]:
        yrs = h["years"]
        span = f"{min(yrs)}–{max(yrs)}，共 {len(yrs)} 版" if len(yrs) > 1 else str(yrs[0])
        console.print(f"  · [bold]{h['title']}[/bold]  [dim]{h['yearbook']} · {h['chapter']} · {span}[/dim]")
        e = h["editions"][0]
        console.print(f"    [blue]{e['url']}[/blue]  [dim]（{e['year']} 版，{e['fmt']}）[/dim]")
    if not res["tables"]:
        console.print(f"[yellow]{res['hint']}[/yellow]")
