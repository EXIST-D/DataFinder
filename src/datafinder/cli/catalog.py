"""命令行：资源目录的检索与获取（search / inspect / facets / sources / get / plan / describe / index / mcp）。

与 MCP 共用 service 层，所以命令行能做的事助手也能做。加 `--json` 得到机器可读输出。
**获取（get）默认只预览**，加 `--yes` 才联网取数/下载并写文件。
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
from typing import Any

import typer
from rich.console import Console
from rich.table import Table as RichTable

from .. import service
from ..service import ServiceError
from ..tables import describe, describe_markdown

console = Console()
err = Console(stderr=True)
index_app = typer.Typer(
    help="资源目录的建立与维护：下载快照、抓取、迁移旧索引、制作快照、状态。", no_args_is_help=True
)

_FREQ = {"annual": "年度", "quarterly": "季度", "monthly": "月度"}
DELIVERY_ICON = {
    "download": "[green]⬇ 可下载[/green]",
    "generate": "[cyan]⚙ 需生成[/cyan]",
    "link": "[yellow]↗ 仅链接[/yellow]",
}


def _run(coro: Any) -> Any:
    try:
        return asyncio.run(coro)
    except ServiceError as exc:
        _fail(exc)
    except Exception as exc:  # noqa: BLE001 —— 命令行边界：翻译成统一的错误输出
        _fail(service.translate(exc))


def _call(fn: Any, *a: Any, **kw: Any) -> Any:
    try:
        return fn(*a, **kw)
    except Exception as exc:  # noqa: BLE001
        _fail(service.translate(exc))


def _fail(exc: ServiceError) -> None:
    tail = f"\n  下一步：{exc.next_action}" if exc.next_action else ""
    err.print(f"[red][{exc.code}] {exc}[/red]{tail}")
    raise typer.Exit(code=1)


def _json(data: Any) -> None:
    typer.echo(json.dumps(data, ensure_ascii=False, indent=2, default=str))


def _item_line(it: dict) -> str:
    period = it["period"]
    span = (
        f"{period['start']}–{'至今' if period['ongoing'] else period['end']}"
        if period["start"]
        else (f"{period['edition_year']} 版" if period["edition_year"] else "")
    )
    vers = (
        f" · {len(it['versions'])} 个版次" if it.get("versions") and len(it["versions"]) > 1 else ""
    )
    return (
        f"  {DELIVERY_ICON[it['delivery']]}  [bold]{it['title']}[/bold]  "
        f"[dim]{it['kind']} · {it['source_id']}{(' · ' + span) if span else ''}{vers}[/dim]"
    )


# ───────────── search / inspect / facets / sources ─────────────


def search(
    query: str = typer.Argument("", help="关键词或一句话；可留空，只用筛选浏览"),
    topic: list[str] = typer.Option([], "--topic", "-t", help="主题 id 或名称（可重复，含下级）"),
    region: list[str] = typer.Option(
        [], "--region", "-r", help="地域：CN-HE / 河北 / 130000（可重复）"
    ),
    kind: list[str] = typer.Option(
        [], "--kind", "-k", help="资源类型：indicator / table / dataset / portal …"
    ),
    delivery: list[str] = typer.Option([], "--delivery", "-d", help="download / generate / link"),
    year_from: int | None = typer.Option(None, "--from", help="观测期起（年）"),
    year_to: int | None = typer.Option(None, "--to", help="观测期止（年）"),
    granularity: str | None = typer.Option(
        None, "--granularity", help="地域粒度：country/province/prefecture/county"
    ),
    strict: bool = typer.Option(False, "--strict", help="高精度：信息未知/未验证的不算满足"),
    limit: int = typer.Option(10, "--limit", "-n", min=1, max=100),
    live: bool = typer.Option(
        False, "--live", help="同时实时查外部数据平台（Zenodo、DataCite…，较慢）"
    ),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """跨 主题 × 地域 × 资源类型 检索资源目录（目录检索不联网）。"""
    res = _run(
        service.search(
            query,
            topic_ids=topic,
            region_ids=region,
            kinds_=kind,
            delivery=delivery,
            period_start=year_from,
            period_end=year_to,
            geo_granularity=granularity,
            mode="strict" if strict else "discovery",
            limit=limit,
            live=live,
        )
    )
    if as_json:
        _json(res)
        return
    console.print(
        f"[dim]共 {res['total']} 条候选（{res['mode']} 模式），显示 {res['returned']} 条[/dim]"
    )
    for it in res["results"]:
        console.print(_item_line(it))
        console.print(f"    [dim]{it['resource_id']}[/dim]")
        for m in it["missing_requirements"][:2]:
            console.print(f"    [yellow]· {m}[/yellow]")
    for p in res.get("portals", [])[:5]:
        console.print(
            f"  {DELIVERY_ICON['link']}  [bold]{p['title']}[/bold]  [dim]门户 · {p['url']}[/dim]"
        )
    for h in res["hints"]:
        console.print(f"[yellow]{h}[/yellow]")
    if res["facets"]:
        top = lambda k, n=6: "  ".join(f"{f['name']} {f['count']}" for f in res["facets"][k][:n])  # noqa: E731
        console.print(
            f"\n[dim]主题：{top('topics')}\n地域：{top('regions')}\n类型：{top('kinds')}[/dim]"
        )
    for d in res.get("live", {}).get("datasets", [])[:5]:
        console.print(f"  [blue]外部[/blue]  {d['title']}  [dim]{d['open_url']}[/dim]")


def inspect_cmd(
    resource_id: str = typer.Argument(..., help="search 结果里的 resource_id"),
    children: bool = typer.Option(False, "--children", "-c", help="列出子资源"),
    limit: int = typer.Option(30, "--limit", "-n"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """看清一个资源：层级、分发入口、怎么获取。"""
    res = _call(service.inspect, resource_id, children=children, limit=limit)
    if as_json:
        _json(res)
        return
    r = res["resource"]
    console.print(" › ".join([*(p["title"] for p in res["parents"]), f"[bold]{r['title']}[/bold]"]))
    console.print(
        f"  {r['kind']} · {r['source_id']} · 核验：{res['verification_level']} · {DELIVERY_ICON[r['delivery']]}"
    )
    tags = [
        f"路径：{r['path']}" if r.get("path") else "",
        f"单位：{r['unit']}" if r.get("unit") else "",
        f"频度：{_FREQ.get(r['frequency'] or '', r['frequency'])}" if r.get("frequency") else "",
    ]
    if any(tags):
        console.print("  " + " ｜ ".join(t for t in tags if t))
    for d in res["distributions"]:
        console.print(
            f"  [dim]入口 {d['access_method']} → {d['delivery']}  {d['url'] or ''} {d['file_format'] or ''} {d['usability'] or ''} {d['note'] or ''}[/dim]"
        )
    console.print(f"  怎么获取：{res['how_to_get']}")
    for c in res.get("children", []):
        console.print(_item_line(c))
        console.print(f"    [dim]{c['resource_id']}[/dim]")
    if res["child_count"] > len(res.get("children", [])):
        console.print(f"  [dim]……共 {res['child_count']} 个子资源，用 --limit 调大[/dim]")


def facets_cmd(as_json: bool = typer.Option(False, "--json")) -> None:
    """合法的主题、地域、资源类型与获取方式（带计数）。"""
    res = _call(service.facets)
    if as_json:
        _json(res)
        return
    t = RichTable(show_header=True, header_style="bold", box=None, pad_edge=False)
    for col in ("主题 id", "名称", "数量"):
        t.add_column(col)
    for x in res["topics"]:
        t.add_row(("  " if x["level"] == 2 else "") + x["id"], x["name"], str(x["count"]))
    console.print(t)
    console.print(
        "\n类型：" + "  ".join(f"{k['id']}（{k['name']} {k['count']}）" for k in res["kinds"])
    )
    console.print("获取方式：" + "  ".join(f"{d['id']} {d['count']}" for d in res["delivery"]))
    console.print("地域：" + "  ".join(f"{r['name']} {r['count']}" for r in res["regions"][:12]))


def sources_cmd(as_json: bool = typer.Option(False, "--json")) -> None:
    """目录里的来源、实时检索来源、门户库规模。"""
    res = _call(service.sources)
    if as_json:
        _json(res)
        return
    t = RichTable(show_header=True, header_style="bold", box=None, pad_edge=False)
    for col in ("来源", "名称", "领域", "能力", "资源数", "状态"):
        t.add_column(col)
    for s in res["catalog_sources"]:
        t.add_row(
            s["source_id"],
            s["name"],
            s["domain_id"],
            ",".join(s["capabilities"]),
            str(s["resources"]),
            s["access_status"],
        )
    console.print(t)
    console.print("\n实时检索来源：" + "、".join(f"{s['title']}" for s in res["live_sources"]))
    console.print(f"门户库：{res['portals']} 条")


# ───────────── get：按获取方式分流，默认只预览 ─────────────


def get_cmd(
    resource_id: str = typer.Argument(..., help="search 结果里的 resource_id"),
    region: list[str] = typer.Option(
        [], "--region", "-r", help="地区名（可重复；“全部”=全部省份）"
    ),
    indicator: list[str] = typer.Option(
        [], "--indicator", "-i", help="数据集级取数时选指标（名称或 id，可重复）"
    ),
    start: str | None = typer.Option(
        None, "--start", help="起期：年度 2015；月度 201501；季度 202401"
    ),
    end: str | None = typer.Option(None, "--end", help="止期，格式同 --start"),
    periods: int = typer.Option(
        8, "--periods", "-n", min=1, max=30, help="不指定起止时取最近多少期"
    ),
    output: str | None = typer.Option(
        None, "--output", "-o", help="保存路径（取数：.csv/.xlsx；下载：目录）；默认存到导出目录"
    ),
    index: int | None = typer.Option(
        None, "--file", help="有多个可下载文件时选第几个（见预览里的 other_options）"
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="确认执行（不加只预览）"),
    parse: bool = typer.Option(
        False,
        "--parse",
        help='强制走"生成"：有网页版的表（同时有 xls 时默认是下载 xls），把网页表格实时解析成整洁长表',
    ),
    no_describe: bool = typer.Option(False, "--no-describe"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """获取一个资源：可直接下载 → 下载；需要生成 → 实时取数并导出；只有链接 → 给出链接。默认只预览，--yes 才执行。"""
    res = _run(
        (service.fetch if parse else service.get)(
            resource_id,
            regions=region or None,
            indicators=indicator or None,
            start=start,
            end=end,
            periods=periods,
            path=output,
            confirm=yes,
            describe=not no_describe,
            trusted=True,
            **({} if parse else {"distribution_index": index}),
        )
    )
    if as_json:
        _json(res)
        return
    status = res["status"]
    if status == "link":
        console.print(f"[yellow]只有链接[/yellow]：{res['landing_url'] or ''}")
        for lk in res["links"]:
            console.print(f"  {lk['url']}  [dim]{lk['note'] or ''}[/dim]")
        console.print(f"[dim]{res['note']}[/dim]")
    elif status == "plan":
        console.print(
            f"[bold]预览[/bold]（未执行）：{res['title']}  {DELIVERY_ICON[res['delivery']]}"
        )
        for k in ("url", "file_format", "usability", "params", "save_to", "license", "warning"):
            if res.get(k):
                console.print(f"  {k}: {res[k]}")
        console.print(f"[yellow]{res['next_action']}[/yellow]")
    elif res.get("rows") is not None:
        console.print(f"[bold]{res['title']}[/bold]  共 {res['row_count']} 行")
        t = RichTable(show_header=True, header_style="bold", box=None, pad_edge=False)
        for col in res["columns"]:
            t.add_column(col)
        for row in res["rows"][:15]:
            t.add_row(*[f"{c:g}" if isinstance(c, float) else str(c) for c in row])
        console.print(t)
        if res.get("describe_markdown"):
            console.print()
            console.print(res["describe_markdown"], markup=False)
        cov = res.get("coverage")
        if cov:
            console.print(
                f"\n覆盖：{cov['observed_cells']}/{cov['expected_cells']} 单元（{cov['status']}，期别口径：{cov['period_basis']}）"
            )
            for m in cov["missing_sample"][:5]:
                console.print(
                    f"  [yellow]缺[/yellow] {m['region']} · {m['indicator']} · {m['period']}"
                )
        console.print(f"\n[green]已保存：[/green]{res['saved_to']}")
    else:
        console.print(
            f"[green]已保存：[/green]{res['saved_to']}（{res['size_bytes']} 字节，{res['file_format']}，{res['usability']}）"
        )


# ───────────── plan / describe / mcp ─────────────


def plan_cmd(
    topic: str = typer.Argument(..., help="研究/分析主题，如 “工业质量体系评价”"),
    region: str | None = typer.Option(None, "--region", "-r", help="地区，如 “河北省”"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """为一个研究主题给出找数方案：指标、数据表、官方入口、检索入口与统计建议。"""
    res = _call(service.plan_sources, topic, region)
    if as_json:
        _json(res)
        return
    u = res["understanding"]
    console.print(
        f"[bold]主题[/bold] {res['topic']}  [dim]地区 {u['regions'] or '未指定'} · 领域 {u['domains'] or '未识别'}[/dim]\n"
    )
    for key in ("indicators", "tables", "documents", "references"):
        g = res["sources"][key]
        console.print(f"[bold]{g['title']}[/bold]")
        for it in g["items"][:10]:
            console.print(_item_line(it))
            console.print(f"    [dim]{it['resource_id']}[/dim]")
        if not g["items"]:
            console.print("  [yellow]没有命中[/yellow]")
        console.print()
    for key in ("regional_portals", "official_portals"):
        g = res["sources"][key]
        if g["items"]:
            console.print(f"[bold]{g['title']}[/bold]")
            for p in g["items"]:
                flag = {
                    "blocked": " [yellow]（拒绝自动访问）[/yellow]",
                    "unavailable": " [yellow]（连不上）[/yellow]",
                    "unverified": " [dim]（未核验）[/dim]",
                }.get(p.get("verify") or "", "")
                console.print(f"  · {p['name']}{flag}  [blue]{p['url']}[/blue]")
            console.print()
    console.print("[bold]继续检索的入口[/bold]")
    for e in res["sources"]["search_entries"]["items"]:
        console.print(f"  · {e['name']}  [blue]{e['url']}[/blue]")
    console.print("\n[bold]统计分析建议[/bold]")
    for h in res["analysis_hints"]:
        console.print(f"  · {h}")
    console.print("\n[dim]" + "；".join(res["caveats"]) + "[/dim]")


def describe_cmd(
    file: str = typer.Argument(..., help="导出的 csv/xlsx（整洁长表）"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """对导出文件计算描述统计。"""
    try:
        result = describe(service.load_table(file, anywhere=True))
    except Exception as exc:  # noqa: BLE001
        _fail(service.translate(exc))
    if as_json:
        _json(result)
    else:
        console.print(describe_markdown(result), markup=False)


def mcp_cmd(
    print_config: bool = typer.Option(
        False, "--print-config", help="只打印可粘贴到 Cursor / Claude 的配置，不启动服务"
    ),
) -> None:
    """启动 MCP 服务（stdio），供 Cursor / Claude 等 AI 助手调用。"""
    if print_config:
        exe = shutil.which("datafinder") or os.path.abspath(sys.argv[0])  # 一定是绝对路径
        server: dict[str, Any] = {"command": exe, "args": ["mcp"]}
        if home := os.environ.get("DATAFINDER_HOME"):  # 数据目录不在默认位置时一并带上
            server["env"] = {"DATAFINDER_HOME": os.path.abspath(home)}
        typer.echo(json.dumps({"mcpServers": {"datafinder": server}}, ensure_ascii=False, indent=2))
        return
    from ..mcp_server import run

    run()


# ───────────── index ─────────────


def _progress(msg: str) -> None:
    console.print(f"  [dim]{msg}[/dim]")


@index_app.command("build")
def index_build(
    adapter: list[str] = typer.Argument(
        None, help="适配器 id（默认全部支持抓取的）；见 datafinder sources"
    ),
    opt: list[str] = typer.Option(
        [], "--opt", "-o", help="适配器参数 key=value，如 db=3,6 / series=cn,CN-SD / from=2015"
    ),
    refresh: bool = typer.Option(False, "--refresh", help="重抓已有内容"),
    max_requests: int | None = typer.Option(
        None, "--max-requests", help="本次最多发多少个请求（分批用）"
    ),
) -> None:
    """抓取资源目录（用户主动触发；串行、限速；遇防护自动停下，再运行同一命令即可续跑）。"""
    params = {}
    for kv in opt:
        if "=" not in kv:
            raise typer.BadParameter(f"--opt 要写成 key=value：{kv}")
        k, v = kv.split("=", 1)
        params[k.strip()] = v.strip()
    console.print(
        "将逐级读取来源的目录：串行、限速，只保存名称/单位/口径/链接，不存任何数值。可随时 Ctrl+C，进度已保存。\n"
    )
    try:
        results = _run(
            service.index.build(
                adapter or None,
                params=params,
                refresh=refresh,
                max_requests=max_requests,
                progress=_progress,
            )
        )
    except KeyboardInterrupt:
        console.print("\n已中断，进度已保存。")
        raise typer.Exit(code=130) from None
    paused = False
    for r in results:
        icon = {
            "done": "[green]✓ 完成[/green]",
            "paused": "[yellow]! 已暂停[/yellow]",
            "limited": "[yellow]! 本批结束[/yellow]",
        }[r["status"]]
        console.print(
            f"\n{icon} {r['adapter']}：新增 {r['added']} 条，请求 {r['requests']} 次。{r['message']}"
        )
        for line in r["stopped"]:
            console.print(f"  [yellow]已放弃（站点拒绝或连不上，不重试不绕过）：{line}[/yellow]")
        paused = paused or r["status"] == "paused"
    if paused:
        raise typer.Exit(code=2)


@index_app.command("migrate")
def index_migrate(adapter: list[str] = typer.Argument(None, help="适配器 id（默认全部）")) -> None:
    """把旧版本地索引一次性迁入统一目录（免去重爬）。迁完后旧库会被改名为 *.migrated。"""
    for r in _call(service.index.migrate, adapter or None):
        if r.get("migrated") is False:
            console.print(f"[dim]{r['adapter']}：{r['reason']}[/dim]")
        else:
            console.print(
                f"[green]✓[/green] {r['adapter']}："
                + "，".join(f"{k} {v}" for k, v in r.items() if k not in ("adapter", "migrated"))
            )


@index_app.command("pull")
def index_pull(
    source: str = typer.Option(
        None,
        "--from",
        help="快照来源：https 基地址或含快照文件的本地目录（默认官方发布）",
    ),
    force: bool = typer.Option(False, "--force", help="本地目录比快照新时也替换"),
) -> None:
    """下载已发布的目录快照并安装（免去自己抓取；旧目录库会备份为 catalog.db.bak）。"""
    r = _call(service.index.pull, source, force=force, progress=_progress)
    m = r["manifest"]
    console.print(
        f"[green]✓[/green] 已安装快照：{m['resources']} 条资源（{m['created_at'][:10]} 制作，"
        f"{m['bytes'] >> 20}MB）"
    )
    for sid, n in sorted(m["by_source"].items(), key=lambda kv: -kv[1])[:6]:
        console.print(f"  [dim]{sid}：{n}[/dim]")
    if r["backup"]:
        console.print(f"[dim]原目录库已备份：{r['backup']}[/dim]")


@index_app.command("probe")
def index_probe(
    url: str = typer.Argument(..., help="统计局站点首页或数据栏目页，如 https://tjj.xx.gov.cn/"),
    editions: int = typer.Option(3, "--editions", "-e", help="往回探几个版次"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """（维护者）扫一个站点：数据栏目、年鉴版次、目录形态能否解析。新省接入前先跑它。"""
    console.print(f"探测 {url}：串行、限速，只读目录页，不取任何数值。\n")
    rep = _run(service.index.probe(url, editions=editions))
    if as_json:
        _json(rep)
        return
    if rep["channels"]:
        table = RichTable(show_header=True, header_style="bold", box=None, pad_edge=False)
        for col in ("类型", "栏目", "地址"):
            table.add_column(col)
        for c in rep["channels"][:12]:
            table.add_row(c["kind"], c["title"], c["url"])
        console.print(table)
    else:
        console.print("[yellow]没找到数据栏目[/yellow]")
    if rep["editions"]:
        console.print(f"\n年鉴版次：{[e['year'] for e in rep['editions']]}")
    best = rep["best"]
    if best:
        console.print(
            f"\n[green]可解析[/green] {best['edition']} 版：{best['format']} 形态，{best['tables']} 张表"
        )
        console.print(f"  目录：{best['toc']}")
        console.print("  可直接写进来源登记：")
        console.print(f"    format: {best['format']}")
        console.print(f'    toc: ["{best["toc"].rsplit("/", 1)[-1]}"]')
        base = best["toc"].rsplit("/", 1)[0] + "/"
        console.print(f'    editions: [{{year: {best["edition"]}, base: "{base}"}}]')
    elif rep["tocs"]:
        console.print(
            "\n[yellow]命中了目录页，但一张表都没解析出来——需要新的目录形态解析器[/yellow]"
        )
    for note in rep["notes"]:
        console.print(f"[dim]{note}[/dim]")


@index_app.command("snapshot")
def index_snapshot(
    out: str = typer.Option("snapshot", "--out", help="输出目录"),
) -> None:
    """（维护者）把当前目录库打包成可发布的快照：datafinder-catalog.db.gz + .json。"""
    m = _call(service.index.snapshot, out)
    console.print(
        f"[green]✓[/green] 已生成快照：{m['resources']} 条，{m['bytes'] >> 20}MB（原 {m['bytes_uncompressed'] >> 20}MB）→ {out}/"
    )
    console.print("[dim]发布：gh release upload catalog-latest <两个文件> --clobber[/dim]")


@index_app.command("status")
def index_status(as_json: bool = typer.Option(False, "--json")) -> None:
    """目录现状。"""
    st = _call(service.status)
    if as_json:
        _json(st)
        return
    c = st["catalog"]
    console.print(
        f"资源目录：{c['resources']} 条  " + "  ".join(f"{k} {v}" for k, v in c["by_kind"].items())
    )
    console.print(f"门户库：{st['portals']} 条；导出目录：{st['exports_dir']}")
    for h in st["hints"]:
        console.print(f"[yellow]{h}[/yellow]")


def register(app: typer.Typer) -> None:
    app.add_typer(index_app, name="index")
    app.command("search")(search)
    app.command("inspect")(inspect_cmd)
    app.command("facets")(facets_cmd)
    app.command("sources")(sources_cmd)
    app.command("get")(get_cmd)
    app.command("plan")(plan_cmd)
    app.command("describe")(describe_cmd)
    app.command("mcp")(mcp_cmd)
