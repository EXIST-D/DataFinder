"""命令行：找数方案（plan）、国家统计局取数（nbs …）、描述统计（describe）、MCP 服务（mcp）。

与 MCP 共用 service 层，所以 CLI 能做的事助手也能做。加 `--json` 得到机器可读输出。
"""

from __future__ import annotations

import asyncio
import json
import shutil
import sys
from typing import Any

import typer
from rich.console import Console
from rich.table import Table as RichTable

from .. import service
from ..domains.official_statistics_cn.nbs import index as nbs_index
from ..domains.official_statistics_cn.nbs.client import DATABASES, NbsClient
from ..engine.cache import Cache
from ..engine.http import Http
from ..finder import MIN_INTERVAL
from ..service import ServiceError
from ..tables import describe, describe_markdown
from .yearbook import yearbook_app

console = Console()
err = Console(stderr=True)
nbs_app = typer.Typer(help="国家统计局：浏览、找指标、取数（逐级、礼貌限速）。", no_args_is_help=True)


def _fail(exc: Exception) -> None:
    err.print(f"[red]{exc}[/red]")
    raise typer.Exit(code=1)


def _json(data: Any) -> None:
    typer.echo(json.dumps(data, ensure_ascii=False, indent=2, default=str))


def _run(coro: Any) -> Any:
    try:
        return asyncio.run(coro)
    except ServiceError as exc:
        _fail(exc)


# ───────────── plan ─────────────


def plan(
    topic: str = typer.Argument(..., help="研究/分析主题，如 “工业质量体系评价”"),
    region: str | None = typer.Option(None, "--region", "-r", help="地区，如 “河北省”"),
    as_json: bool = typer.Option(False, "--json", help="输出 JSON"),
) -> None:
    """为一个研究主题给出找数方案：可直接取表的指标、官方入口、检索入口与统计建议。"""
    try:
        result = service.plan_sources(topic, region)
    except ServiceError as exc:
        _fail(exc)
    if as_json:
        _json(result)
        return
    u = result["understanding"]
    console.print(
        f"[bold]主题[/bold] {result['topic']}  [dim]地区 {u['regions'] or '未指定'} · "
        f"领域 {u['domains'] or '未识别'}[/dim]\n"
    )
    src = result["sources"]
    direct = src["direct"]
    console.print(f"[bold]{direct['title']}[/bold]")
    if direct["items"]:
        for it in direct["items"][:10]:
            tag = "分省" if it["regional"] else "全国"
            console.print(
                f"  · [{tag}] {it['indicator']}  [dim]{it['path']}"
                f"  db={it['db']} cid={it['cid'][:8]}…[/dim]"
            )
        console.print("  [dim]取表：datafinder nbs get --db … --cid … -i 指标 -r 地区（也可用 plan --json 拿完整参数）[/dim]")
    else:
        console.print(f"  [yellow]{direct['hint'] or '没有命中指标'}[/yellow]")
    yb = src.get("yearbooks", {})
    if yb.get("items") or yb.get("hint"):
        console.print(f"\n[bold]{yb['title']}[/bold]")
        for h in yb["items"][:6]:
            yrs = h["years"]
            span = f"{min(yrs)}–{max(yrs)}（{len(yrs)} 版）" if len(yrs) > 1 else f"{yrs[0]} 版"
            console.print(f"  · {h['title']}  [dim]{h['yearbook']} · {span}[/dim]")
            console.print(f"    [blue]{h['editions'][0]['url']}[/blue]")
        if not yb["items"]:
            console.print(f"  [yellow]{yb['hint']}[/yellow]")
    for key in ("regional_portals", "official_portals"):
        group = src[key]
        if not group["items"]:
            continue
        console.print(f"\n[bold]{group['title']}[/bold]")
        for p in group["items"]:
            flag = {"ok": "", "blocked": " [yellow]（拒绝自动访问）[/yellow]", "unverified": " [dim]（未核验）[/dim]"}.get(
                p.get("verify") or "", ""
            )
            console.print(f"  · {p['name']}{flag}  [blue]{p['url']}[/blue]")
            if p.get("note"):
                console.print(f"    [dim]{p['note']}[/dim]")
    console.print(f"\n[bold]{src['search_entries']['title']}[/bold]")
    for e in src["search_entries"]["items"]:
        console.print(f"  · {e['name']}  [blue]{e['url']}[/blue]")
    console.print("\n[bold]统计分析建议[/bold]")
    for h in result["analysis_hints"]:
        console.print(f"  · {h}")
    console.print("\n[dim]" + "；".join(result["caveats"]) + "[/dim]")


# ───────────── nbs ─────────────


@nbs_app.command("dbs")
def nbs_dbs() -> None:
    """列出 13 个数据库。"""
    t = RichTable(show_header=True, header_style="bold", box=None, pad_edge=False)
    for col in ("代码", "名称", "时间", "分地区"):
        t.add_column(col)
    for d in service.nbs_databases():
        t.add_row(d["db"], d["name"], d["time_type"], "是" if d["regional"] else "")
    console.print(t)


@nbs_app.command("tree")
def nbs_tree(
    db: str = typer.Argument(..., help="数据库代码，见 nbs dbs"),
    node: str = typer.Argument("", help="分类节点 id；省略则看第一层"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """逐级浏览分类树。叶子节点的 id 就是 cid。"""
    res = _run(service.nbs_browse(db, node))
    if as_json:
        _json(res)
        return
    for n in res["nodes"]:
        kind = "[green]数据集[/green]" if n["is_leaf"] else "分类"
        console.print(f"  {kind}  {n['name']}  [dim]{n['id']}[/dim]")


@nbs_app.command("indicators")
def nbs_inds(
    cid: str = typer.Argument(..., help="叶子数据集 id"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """列出一个数据集下的指标与可选地区。"""
    res = _run(service.nbs_indicators(cid))
    if as_json:
        _json(res)
        return
    for i in res["indicators"]:
        console.print(f"  · {i['name']}  [dim]{i['id']}{' · ' + i['unit'] if i.get('unit') else ''}[/dim]")
    names = "、".join(r["text"] for r in res["regions"][:10])
    console.print(f"\n[dim]地区（前 10）：{names}[/dim]")


@nbs_app.command("find")
def nbs_find(
    query: str = typer.Argument(..., help="关键词，如 “规模以上工业企业 利润”"),
    db: str | None = typer.Option(None, "--db", help="只在这个库里找"),
    regional: bool | None = typer.Option(None, "--regional/--national", help="只看分地区的库 / 只看全国口径"),
    limit: int = typer.Option(15, "--limit", "-n"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """在本地指标索引里找指标（先运行 nbs index 建索引）。"""
    try:
        res = service.nbs_find(query, db=db, regional=regional, limit=limit)
    except ServiceError as exc:
        _fail(exc)
    if as_json:
        _json(res)
        return
    for h in res["hits"]:
        console.print(f"  · {h['indicator']}  [dim]{h['database']} db={h['db']} cid={h['cid']} id={h['indicator_id']}[/dim]")
        console.print(f"    [dim]{h['path']}{' · ' + h['unit'] if h.get('unit') else ''}[/dim]")
    if not res["hits"]:
        console.print(f"[yellow]{res['hint']}[/yellow]")


@nbs_app.command("index")
def nbs_build_index(
    db: str = typer.Option(",".join(nbs_index.DEFAULT_DBS), "--db", help="要索引的库代码，逗号分隔"),
    max_requests: int | None = typer.Option(None, "--max-requests", help="本次最多发多少个请求（用于分批）"),
) -> None:
    """建立国家统计局指标索引（一次性；遇到访问防护会自动停下，再运行同一命令即可续跑）。"""
    codes = [c.strip() for c in db.split(",") if c.strip()]
    bad = [c for c in codes if c not in DATABASES]
    if bad:
        raise typer.BadParameter(f"未知数据库代码：{', '.join(bad)}（见 datafinder nbs dbs）")
    console.print(
        "将逐级读取国家统计局的分类树与指标清单：串行、每次间隔 ≥1 秒，只保存指标名称/单位/口径，不存数值。\n"
        "可随时 Ctrl+C 中断，进度已保存，再运行同一命令即可续跑。\n"
    )

    async def go() -> nbs_index.BuildResult:
        http = Http(min_interval={**MIN_INTERVAL, "data.stats.gov.cn": 1.0})
        cache = Cache()
        conn = nbs_index.connect_index()
        try:
            return await nbs_index.build(
                NbsClient(http, cache), conn, codes, progress=lambda m: console.print(f"  [dim]{m}[/dim]"),
                max_requests=max_requests,
            )
        finally:
            conn.close()
            cache.close()
            await http.aclose()

    try:
        res = asyncio.run(go())
    except KeyboardInterrupt:
        console.print("\n已中断，进度已保存。")
        raise typer.Exit(code=130) from None
    icon = {"done": "[green]✓ 完成[/green]", "paused": "[yellow]! 已暂停[/yellow]", "limited": "[yellow]! 本批结束[/yellow]"}[res.status]
    console.print(f"\n{icon}：新增数据集 {res.leaves_done} 个、指标 {res.indicators_added} 条，请求 {res.requests} 次。{res.message}")
    conn = nbs_index.connect_index()
    console.print(f"[dim]索引现状：{nbs_index.stats(conn)['indicators']} 条指标[/dim]")
    conn.close()
    if res.status == "paused":
        raise typer.Exit(code=2)


@nbs_app.command("get")
def nbs_get(
    db: str = typer.Option(..., "--db", help="数据库代码，如 6（分省年度）"),
    cid: str = typer.Option(..., "--cid", help="叶子数据集 id（nbs find / tree 得到）"),
    indicator: list[str] = typer.Option(..., "--indicator", "-i", help="指标 id 或名称，可重复"),
    region: list[str] = typer.Option([], "--region", "-r", help="地区名，可重复；“全部”=全部省份；全国口径库可省略"),
    start: str | None = typer.Option(None, "--start", help="起期：年度 2015；月度 201501；季度 202401"),
    end: str | None = typer.Option(None, "--end", help="止期，格式同 --start"),
    n: int = typer.Option(8, "--periods", "-n", min=1, max=30, help="不指定起止时取最近多少期"),
    output: str | None = typer.Option(None, "--output", "-o", help="保存路径，.csv 或 .xlsx；默认存到导出目录"),
    no_describe: bool = typer.Option(False, "--no-describe", help="不输出描述统计"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """取数并导出 CSV / Excel，同时给出描述统计。"""

    async def go() -> dict[str, Any]:
        table = await service.nbs_fetch(db, cid, indicator, region or None, start=start, end=end, n=n)
        return service.table_result(table, path=output, stats=not no_describe)

    res = _run(go())
    if as_json:
        _json(res)
        return
    console.print(f"[bold]{res['title']}[/bold]  共 {res['row_count']} 行")
    t = RichTable(show_header=True, header_style="bold", box=None, pad_edge=False)
    for col in res["columns"]:
        t.add_column(col)
    for row in res["rows"][:15]:
        t.add_row(*[f"{c:g}" if isinstance(c, float) else str(c) for c in row])
    console.print(t)
    if res["row_count"] > 15:
        console.print(f"[dim]……其余 {res['row_count'] - 15} 行见文件[/dim]")
    if res.get("describe_markdown"):
        console.print()
        console.print(res["describe_markdown"], markup=False)
    console.print(f"\n[green]已保存：[/green]{res['saved_to']}")


# ───────────── describe / mcp ─────────────


def describe_cmd(
    file: str = typer.Argument(..., help="导出的 csv/xlsx（整洁长表）"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """对导出文件计算描述统计。"""
    try:
        result = describe(service.load_table(file, anywhere=True))
    except (ServiceError, ValueError) as exc:
        _fail(exc)
    if as_json:
        _json(result)
    else:
        console.print(describe_markdown(result), markup=False)


def mcp_cmd(
    print_config: bool = typer.Option(False, "--print-config", help="只打印可粘贴到 Cursor / Claude 的配置，不启动服务"),
) -> None:
    """启动 MCP 服务（stdio），供 Cursor / Claude 等 AI 助手调用。"""
    if print_config:
        exe = shutil.which("datafinder") or sys.argv[0]
        cfg = {"mcpServers": {"datafinder": {"command": exe, "args": ["mcp"]}}}
        typer.echo(json.dumps(cfg, ensure_ascii=False, indent=2))
        return
    from ..mcp_server import run

    run()


def register(app: typer.Typer) -> None:
    app.add_typer(nbs_app, name="nbs")
    app.add_typer(yearbook_app, name="yearbook")
    app.command("plan")(plan)
    app.command("describe")(describe_cmd)
    app.command("mcp")(mcp_cmd)
