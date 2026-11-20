"""命令行入口。命令名：`datafinder`（短别名 `dfd`）。"""

from __future__ import annotations

import asyncio
import json
import sqlite3

import typer
from rich.console import Console
from rich.progress import BarColumn, DownloadColumn, Progress, TextColumn, TransferSpeedColumn
from rich.table import Table

from . import __version__
from .bench import bench_app
from .doctor import run_checks
from .finder import search_datasets
from .models import Dataset
from .portals.search import PortalHit, search_portals
from .portals.sync import sync as run_sync
from .portals.tag import industry_zh, load_config
from .query.plan import country_zh_name
from .sources.registry import load_specs
from .store import connect, db_path, portal_count

app = typer.Typer(
    help="DataFinder：中文优先的找数入口。",
    no_args_is_help=True,
    add_completion=False,
)
console = Console()
app.add_typer(bench_app, name="bench")

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


# ───────────── 数据集检索 ─────────────

_STATUS_ICON = {
    "ok": "[green]✓[/green]",
    "cached": "[green]✓[/green]",
    "stale": "[yellow]≈[/yellow]",
    "skipped": "[dim]–[/dim]",
    "resting": "[yellow]z[/yellow]",
    "error": "[red]✗[/red]",
    "timeout": "[red]✗[/red]",
    "not_waited": "[yellow]…[/yellow]",
}


def _fmt_size(n: int | None) -> str | None:
    if not n:
        return None
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f}{unit}" if unit == "B" else f"{n:.1f}{unit}"
        n /= 1024
    return None


def _print_dataset(
    rank: int, d: Dataset, titles: dict[str, str], *, explain: bool, resources: bool
) -> None:
    where = "、".join(titles.get(s, s) for s in d.sources) or titles.get(d.source, d.source)
    console.print(f"[bold]{rank:>2}. {d.title}[/bold]")
    facts = [
        f"[cyan]{where}[/cyan]",
        d.license or "[yellow]许可未知[/yellow]",
    ]
    if d.publisher and d.publisher not in where:
        facts.append(d.publisher[:40])
    if d.period:
        facts.append(f"数据期 {d.period}")
    if d.updated:
        facts.append(f"更新 {d.updated}")
    if d.formats:
        facts.append("/".join(d.formats[:5]))
    console.print("    " + " · ".join(facts))
    if d.description:
        text = d.description if len(d.description) <= 110 else d.description[:109] + "…"
        console.print(f"    {text}")
    console.print(f"    [blue]{d.url}[/blue]")
    if resources:
        if not d.resources:
            console.print("    [dim]资源：上游未提供文件/接口列表[/dim]")
        for r in d.resources[:6]:
            extra = " · ".join(x for x in (r.format, _fmt_size(r.size_bytes)) if x)
            console.print(
                f"    [dim]- {r.name}（{r.kind}{' · ' + extra if extra else ''}）[/dim] {r.url}"
            )
            if r.note:
                console.print(f"      [dim]{r.note}[/dim]")
            if r.body:
                console.print(f"      [dim]请求体：{r.body}[/dim]")
    if explain:
        parts = " ".join(f"{k}={v:.3g}" for k, v in d.explain.items())
        console.print(f"    [dim]得分 {d.score:.4f}：{parts}[/dim]")


def _dataset_json(d: Dataset) -> dict:
    out = d.to_dict()
    out["score"] = round(d.score, 6)
    return out


@app.command()
def search(
    query: str = typer.Argument(..., help="关键词或一句话，如 “中国各省近十年人口 GDP 城镇化率”"),
    limit: int = typer.Option(10, "--limit", "-n", min=1, max=50, help="返回条数"),
    source: str | None = typer.Option(
        None, "--source", "-s", help="只查这些来源，逗号分隔（见 sources 命令）"
    ),
    exclude: str | None = typer.Option(None, "--exclude", "-x", help="排除这些来源，逗号分隔"),
    resources: bool = typer.Option(False, "--resources", "-r", help="显示每条的下载/API 资源"),
    portals_n: int = typer.Option(3, "--portals", help="同时列出相关门户的条数，0 表示不列"),
    refresh: bool = typer.Option(False, "--refresh", help="忽略缓存，重新查询"),
    timeout: float = typer.Option(20.0, "--timeout", min=3, max=120, help="总截止时间（秒）"),
    as_json: bool = typer.Option(False, "--json", help="输出 JSON"),
    explain: bool = typer.Option(False, "--explain", help="显示排序依据与查询计划"),
) -> None:
    """查找数据集：一次查多个来源，合并去重，给出原站链接与下载/API 资源。"""
    only = {x.strip() for x in source.split(",") if x.strip()} if source else None
    skip = {x.strip() for x in exclude.split(",") if x.strip()} if exclude else None
    try:
        with console.status("正在查询各来源…", spinner="dots") if not as_json else _null():
            result = asyncio.run(
                search_datasets(
                    query, limit=limit, only=only, exclude=skip, refresh=refresh, deadline=timeout
                )
            )
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc

    titles = {s.id: s.title for s in load_specs()}
    portal_hits = []
    if portals_n and not as_json:
        conn = connect()
        if portal_count(conn):
            _, portal_hits = search_portals(conn, query, limit=portals_n)
    if as_json:
        typer.echo(
            json.dumps(
                {
                    "query": query,
                    "complete": result.complete,
                    "datasets": [_dataset_json(d) for d in result.datasets],
                    "sources": [
                        {
                            "id": s.id,
                            "status": s.status,
                            "elapsed_ms": s.elapsed_ms,
                            "count": s.count,
                            "message": s.message,
                        }
                        for s in result.statuses
                    ],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    if explain:
        p = result.plan
        console.print(
            f"[dim]查询计划：概念 {[c.label for c in p.concepts]} · 英文检索词 {p.query_en()!r}"
            f" · 中文检索词 {p.zh_terms()} · 地点 {p.location_zh} · 格式 {p.formats} · 年份 {p.years}[/dim]\n"
        )
    if not result.datasets:
        console.print("没有找到匹配的数据集。可以换个说法、去掉格式词，或用 --source 指定来源。")
    for i, d in enumerate(result.datasets, start=1):
        _print_dataset(i, d, titles, explain=explain, resources=resources)

    status_line = "  ".join(
        f"{_STATUS_ICON[s.status]}{s.title}{f' {s.count}' if s.answered else ''}"
        for s in result.statuses
    )
    console.print(f"\n[dim]来源：[/dim]{status_line}")
    for s in result.statuses:
        if s.status in ("error", "timeout", "resting", "stale", "not_waited") and s.message:
            console.print(f"  [dim]{s.title}：{s.message}[/dim]")
    if not result.complete:
        console.print("[yellow]部分来源没有返回，结果可能不完整。[/yellow]")
    if portal_hits:
        console.print("\n[bold]相关门户[/bold]（可以去这些网站自己找）")
        for h in portal_hits:
            row = h.row
            console.print(f"  · {row['name_zh'] or row['name']}  [blue]{row['url']}[/blue]")
    if any(d.license is None for d in result.datasets):
        console.print("[dim]许可未知不代表可随意使用，请以原站声明为准。[/dim]")


class _null:
    def __enter__(self) -> None:
        return None

    def __exit__(self, *exc: object) -> None:
        return None


@app.command()
def sources() -> None:
    """列出数据集来源及其状态。"""
    from .sources.dataseek import find_executable

    table = Table(show_header=True, header_style="bold", box=None, pad_edge=False)
    for col in ("id", "名称", "协议", "状态", "官方", "语言"):
        table.add_column(col)
    for s in load_specs():
        status = {
            "active": "[green]启用[/green]",
            "experimental": "[yellow]实验性[/yellow]",
            "disabled": "[dim]停用[/dim]",
        }[s.status]
        table.add_row(
            s.id, s.title, s.protocol, status, "是" if s.official else "", "/".join(s.langs)
        )
    console.print(table)
    exe = find_executable()
    console.print(f"\n[dim]Dataseek 桥接：{exe or '未安装（可选）'}[/dim]")


@app.command()
def serve(
    port: int = typer.Option(8787, "--port", "-p", min=1024, max=65535, help="端口"),
    open_browser: bool = typer.Option(True, "--open/--no-open", help="启动后自动打开浏览器"),
) -> None:
    """启动本地网页（仅监听本机 127.0.0.1）。"""
    import socket
    import threading
    import webbrowser

    import uvicorn

    from .server.app import create_app

    with socket.socket() as probe:
        if probe.connect_ex(("127.0.0.1", port)) == 0:
            console.print(
                f"[red]端口 {port} 已被占用。[/red]换一个：datafinder serve --port {port + 1}"
            )
            raise typer.Exit(code=1)
    conn = connect()
    if portal_count(conn) == 0:
        console.print(
            "[yellow]提示：门户库还是空的，右侧不会显示门户。运行 datafinder sync 后刷新页面。[/yellow]"
        )
    conn.close()
    url = f"http://127.0.0.1:{port}"
    console.print(f"DataFinder 网页已启动：[blue]{url}[/blue]（Ctrl+C 退出）")
    if open_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    uvicorn.run(create_app(), host="127.0.0.1", port=port, log_level="warning")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
