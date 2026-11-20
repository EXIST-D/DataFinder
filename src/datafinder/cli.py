"""命令行入口。命令名：`datafinder`（短别名 `dfd`）。"""

from __future__ import annotations

import json
import sqlite3

import typer
from rich.console import Console
from rich.progress import BarColumn, DownloadColumn, Progress, TextColumn, TransferSpeedColumn
from rich.table import Table

from . import __version__
from .doctor import run_checks
from .portals.search import PortalHit, search_portals
from .portals.sync import sync as run_sync
from .portals.tag import industry_zh, load_config
from .query.plan import country_zh_name
from .store import connect, db_path, portal_count

app = typer.Typer(
    help="DataFinder：中文优先的找数入口。",
    no_args_is_help=True,
    add_completion=False,
)
console = Console()

_ICON = {"ok": "[green]✓[/green]", "warn": "[yellow]![/yellow]", "fail": "[red]✗[/red]"}


def _version_callback(value: bool) -> None:
    if value:
        console.print(f"datafinder {__version__}")
        raise typer.Exit()


@app.callback()
def _root(
    version: bool = typer.Option(
        False, "--version", "-V", callback=_version_callback, is_eager=True, help="显示版本"
    ),
) -> None:
    """DataFinder：中文优先的找数入口。"""


@app.command()
def doctor(
    offline: bool = typer.Option(False, "--offline", help="跳过需要联网的检查"),
) -> None:
    """一键体检：环境、网络、门户库、可选组件是否就绪。"""
    table = Table(show_header=False, box=None, pad_edge=False)
    checks = run_checks(offline=offline)
    for c in checks:
        table.add_row(_ICON[c.status], f"[bold]{c.name}[/bold]", c.detail)
    console.print(table)
    if any(c.status == "fail" for c in checks):
        raise typer.Exit(code=1)


@app.command()
def sync(
    force: bool = typer.Option(False, "--force", "-f", help="强制重新下载门户库"),
) -> None:
    """同步门户库：下载 Dateno 注册表，打行业标签，叠加精选，写入本地 SQLite。"""
    conn = connect()
    with Progress(
        TextColumn("[bold]下载门户库[/bold]"),
        BarColumn(),
        DownloadColumn(),
        TransferSpeedColumn(),
        console=console,
        transient=True,
    ) as progress:
        task = progress.add_task("dl", total=None)

        def on_progress(done: int, total: int | None) -> None:
            progress.update(task, completed=done, total=total)

        result = run_sync(conn, force=force, on_progress=on_progress)
    console.print(
        f"[green]✓[/green] 门户库已更新：[bold]{result.total}[/bold] 条"
        f"（来源 Dateno 注册表 {result.registry_ref}，CC BY 4.0），耗时 {result.seconds:.1f}s"
    )
    console.print(
        f"  精选标注 {result.curated_applied} 条（其中新增 {result.curated_added} 条）"
        f"，数据库：{db_path()}"
    )
    if result.curated_missing:
        console.print(
            f"[yellow]!  {len(result.curated_missing)} 条精选在注册表里没匹配到[/yellow]（已跳过）："
        )
        for link in result.curated_missing:
            console.print(f"   - {link}")


def _type_zh(catalog_type: str | None) -> str:
    if not catalog_type:
        return "未知类型"
    info = load_config().catalog_types.get(catalog_type)
    return info["zh"] if info else catalog_type


def _location(row: sqlite3.Row) -> str:
    country = row["country"]
    name = (country_zh_name(country) or row["country_en"] or country) if country else "未知地区"
    return f"{name}·{row['region_zh']}" if row["region_zh"] else name


def _snippet(row: sqlite3.Row, width: int = 90) -> str:
    text = (row["desc_zh"] or row["description"] or "").replace("\n", " ").strip()
    return text if len(text) <= width else text[: width - 1] + "…"


def _print_hit(rank: int, hit: PortalHit, *, explain: bool) -> None:
    row = hit.row
    title = row["name_zh"] or row["name"]
    badges = []
    if row["featured"]:
        badges.append("[yellow]★精选[/yellow]")
    if row["is_national"]:
        badges.append("[cyan]国家级[/cyan]")
    if row["has_api"]:
        badges.append("[green]API[/green]")
    other = f" [dim]{row['name']}[/dim]" if row["name_zh"] and row["name_zh"] != row["name"] else ""
    console.print(f"[bold]{rank:>2}.[/bold] [bold]{title}[/bold]{other}  {' '.join(badges)}")
    inds = " ".join(industry_zh(i) for i in hit.industries) or "—"
    console.print(f"    [dim]{_location(row)} · {_type_zh(row['catalog_type'])} · {inds}[/dim]")
    snippet = _snippet(row)
    if snippet:
        console.print(f"    {snippet}")
    console.print(f"    [blue]{row['url']}[/blue]")
    if explain:
        parts = " ".join(f"{k}×{v:.2f}" for k, v in hit.explain.items())
        console.print(
            f"    [dim]得分 {hit.score:.2f} = bm25 {parts}；命中 {','.join(hit.matched)}[/dim]"
        )


def _resolve_industry(value: str | None) -> str | None:
    if not value:
        return None
    for ind in load_config().industries.values():
        if value in (ind.id, ind.zh, ind.en):
            return ind.id
    valid = "、".join(f"{i.zh}({i.id})" for i in load_config().industries.values())
    raise typer.BadParameter(f"未知行业 {value!r}。可选：{valid}")


def _resolve_type(value: str | None) -> str | None:
    if not value:
        return None
    for en, info in load_config().catalog_types.items():
        if value in (en, info["zh"]):
            return en
    valid = "、".join(info["zh"] for info in load_config().catalog_types.values())
    raise typer.BadParameter(f"未知类型 {value!r}。可选：{valid}")


@app.command()
def portals(
    query: str = typer.Argument("", help="关键词或一句话，如 “气象 观测” “人口普查” “air quality”"),
    country: str | None = typer.Option(None, "--country", "-c", help="国家/地区代码，如 CN、US"),
    type_: str | None = typer.Option(None, "--type", "-t", help="门户类型，如 开放数据门户"),
    industry: str | None = typer.Option(None, "--industry", "-i", help="行业，如 气象气候"),
    api: bool = typer.Option(False, "--api", help="只看提供 API 的门户"),
    limit: int = typer.Option(10, "--limit", "-n", min=1, max=100, help="返回条数"),
    as_json: bool = typer.Option(False, "--json", help="输出 JSON"),
    explain: bool = typer.Option(False, "--explain", help="显示排序依据"),
) -> None:
    """查找数据门户：哪些网站可能有这类数据。"""
    conn = connect()
    if portal_count(conn) == 0:
        console.print("[yellow]门户库还是空的。请先运行：[/yellow] datafinder sync")
        raise typer.Exit(code=1)
    plan, hits = search_portals(
        conn,
        query,
        country=country,
        type_=_resolve_type(type_),
        industry=_resolve_industry(industry),
        api_only=api,
        limit=limit,
    )
    if as_json:
        out = [
            {
                "name": h.row["name"],
                "name_zh": h.row["name_zh"],
                "url": h.row["url"],
                "country": h.row["country"],
                "region": h.row["region"],
                "catalog_type": h.row["catalog_type"],
                "industries": h.industries,
                "api": bool(h.row["has_api"]),
                "api_url": h.row["api_url"],
                "featured": bool(h.row["featured"]),
                "is_national": bool(h.row["is_national"]),
                "source": h.row["source"],
                "score": round(h.score, 4),
                "matched": h.matched,
            }
            for h in hits
        ]
        typer.echo(json.dumps(out, ensure_ascii=False, indent=2))
        return
    if not hits:
        console.print("没有找到匹配的门户。可以换个说法，或去掉过滤条件再试。")
        raise typer.Exit(code=0)
    for i, hit in enumerate(hits, start=1):
        _print_hit(i, hit, explain=explain)
    console.print(
        f"\n[dim]共显示 {len(hits)} 条 · 门户目录来自 Dateno 注册表（CC BY 4.0），"
        "收录不代表已实时核验，请以原站为准。[/dim]"
    )


def main() -> None:
    app()


if __name__ == "__main__":
    main()
