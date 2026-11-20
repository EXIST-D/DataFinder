"""中文找数基准：用数据判断"改动值不值得"，而不是凭感觉调参。

用法：
    datafinder bench pool    # 汇集各方案 Top-N 里还没标注的候选 → bench/out/pool.tsv，供人/Agent 标注
    datafinder bench run     # 在标注好的问句上比较各方案：nDCG@10、P@5

方案（同一批检索结果上对比，所以差异只来自排序，不是网络波动）：
- datafinder        完整引擎（RRF × 概念覆盖度 × 加成）
- 无加成 / 无覆盖度 / 仅 RRF   逐项关掉，看每一项的贡献
- dsk-原句          把用户原句（中文）直接交给 Dataseek，用它自己的排序——"黑盒封装"基线
- dsk-英文词        把我们翻译好的英文检索词交给 Dataseek，用它自己的排序——看"翻译"单独的价值

标注文件 bench/judgments.tsv：qid<TAB>key<TAB>grade<TAB>labeler<TAB>title。
grade：2 直接相关 / 1 部分相关 / 0 无关（口径见 queries.zh.yaml 的 intent）。
**标注者必须写明**（agent 或 human），不能让机器标注冒充人工。
未标注的结果按 0 分计，并在报告里显示"Top10 里未标注数"，数字大说明需要重新 pool。

局限：只有 14 条问句、标注者是 Agent、结果随网络实时变化，只能当方向性证据，不是统计结论。
"""

from __future__ import annotations

import asyncio
import csv
import json
import math
from dataclasses import dataclass
from pathlib import Path

import typer
import yaml
from rich.console import Console
from rich.table import Table

from ..engine.dedup import identity_keys
from ..engine.http import Http
from ..finder import MIN_INTERVAL, rank_with, retrieve
from ..models import Dataset
from ..sources.base import Ctx, SourceSpec
from ..sources.dataseek import DataseekSource

console = Console()
err = Console(stderr=True)  # 进度信息走 stderr，保证 --json 的 stdout 干净
bench_app = typer.Typer(help="中文找数基准（评估排序与来源）。", no_args_is_help=True)

K = 10
VARIANTS = ["datafinder", "无加成", "无覆盖度", "仅RRF", "dsk-原句", "dsk-英文词"]


@dataclass
class BenchQuery:
    id: str
    query: str
    intent: str


def load_queries(directory: Path) -> list[BenchQuery]:
    rows = yaml.safe_load((directory / "queries.zh.yaml").read_text(encoding="utf-8"))
    return [BenchQuery(r["id"], r["query"], r["intent"]) for r in rows]


def load_judgments(directory: Path) -> dict[str, dict[str, int]]:
    path = directory / "judgments.tsv"
    out: dict[str, dict[str, int]] = {}
    if not path.exists():
        return out
    with path.open(encoding="utf-8", newline="") as fh:
        for row in csv.reader(fh, delimiter="\t"):
            if not row or row[0].startswith("#") or len(row) < 4:
                continue
            qid, key, grade = row[0], row[1], row[2]
            out.setdefault(qid, {})[key] = int(grade)
    return out


def keys_of(d: Dataset) -> list[str]:
    strong, titles = identity_keys(d)
    return [*strong, *titles]


def grade_of(d: Dataset, judged: dict[str, int]) -> int | None:
    for k in keys_of(d):
        if k in judged:
            return judged[k]
    return None


def ndcg_at_k(grades: list[int], ideal_pool: list[int], k: int = K) -> float:
    def dcg(gs: list[int]) -> float:
        return sum((2**g - 1) / math.log2(i + 2) for i, g in enumerate(gs[:k]))

    ideal = dcg(sorted(ideal_pool, reverse=True))
    return dcg(grades) / ideal if ideal > 0 else 0.0


def precision_at(grades: list[int], k: int = 5) -> float:
    top = grades[:k]
    return sum(1 for g in top if g >= 1) / k if top else 0.0


async def _dsk(query: str) -> list[Dataset]:
    bridge = DataseekSource(SourceSpec(id="dataseek", title="dsk", protocol="dataseek"))
    if bridge.exe is None:
        return []
    http = Http(min_interval=MIN_INTERVAL)
    try:
        return await bridge.fetch(Ctx(http), query, 15)
    finally:
        await http.aclose()


async def run_variants(q: BenchQuery, *, refresh: bool) -> dict[str, list[Dataset]]:
    r = await retrieve(q.query, refresh=refresh)
    out: dict[str, list[Dataset]] = {}
    for name, kw in {
        "datafinder": {},
        "无加成": {"use_boosts": False},
        "无覆盖度": {"use_coverage": False},
        "仅RRF": {"use_coverage": False, "use_boosts": False},
    }.items():
        out[name], _ = rank_with(r.plan, r.sources, r.per_source, **kw)
    try:
        out["dsk-原句"] = await _dsk(q.query)
    except Exception:  # 基线失败不应拖垮整个基准
        out["dsk-原句"] = []
    try:
        en = r.plan.query_en()
        out["dsk-英文词"] = await _dsk(en) if en else []
    except Exception:
        out["dsk-英文词"] = []
    return out


# ───────────── 命令 ─────────────

DIR_OPT = typer.Option(
    Path("bench"), "--dir", help="基准目录（含 queries.zh.yaml 与 judgments.tsv）"
)


@bench_app.command("pool")
def pool(
    top: int = typer.Option(12, "--top", "-n", help="每个方案取前 N 条"),
    directory: Path = DIR_OPT,
    refresh: bool = typer.Option(False, "--refresh", help="忽略缓存重新检索"),
) -> None:
    """汇集各方案 Top-N 里还没标注的候选，写入 bench/out/pool.tsv。"""
    queries = load_queries(directory)
    judgments = load_judgments(directory)
    out_dir = directory / "out"
    out_dir.mkdir(parents=True, exist_ok=True)
    rows: list[list[str]] = []
    for q in queries:
        err.print(f"[dim]{q.id} {q.query}[/dim]")
        variants = asyncio.run(run_variants(q, refresh=refresh))
        seen: set[str] = set()
        for items in variants.values():
            for d in items[:top]:
                keys = keys_of(d)
                if not keys or keys[0] in seen or grade_of(d, judgments.get(q.id, {})) is not None:
                    continue
                seen.add(keys[0])
                desc = (d.description or "").replace("\t", " ").replace("\n", " ")[:160]
                rows.append(
                    [q.id, keys[0], d.title.replace("\t", " ")[:140], ",".join(d.sources[:2]), desc]
                )
    path = out_dir / "pool.tsv"
    with path.open("w", encoding="utf-8", newline="") as fh:
        csv.writer(fh, delimiter="\t").writerows(rows)
    console.print(f"[green]✓[/green] 待标注 {len(rows)} 条 → {path}")


@bench_app.command("run")
def run(
    directory: Path = DIR_OPT,
    refresh: bool = typer.Option(False, "--refresh", help="忽略缓存重新检索"),
    as_json: bool = typer.Option(False, "--json", help="输出 JSON"),
) -> None:
    """在已标注的问句上比较各方案。"""
    queries = [
        q for q in load_queries(directory) if q.id in (judgments := load_judgments(directory))
    ]
    if not queries:
        console.print("[yellow]还没有任何标注。先运行 datafinder bench pool 并标注。[/yellow]")
        raise typer.Exit(code=1)
    per_query: dict[str, dict[str, dict[str, float]]] = {}
    for q in queries:
        judged = judgments[q.id]
        ideal = list(judged.values())
        variants = asyncio.run(run_variants(q, refresh=refresh))
        stats: dict[str, dict[str, float]] = {}
        for name in VARIANTS:
            items = variants.get(name, [])[:K]
            graded = [grade_of(d, judged) for d in items]
            grades = [g or 0 for g in graded]
            stats[name] = {
                "ndcg": ndcg_at_k(grades, ideal),
                "p5": precision_at(grades),
                "unjudged": float(sum(1 for g in graded if g is None)),
                "n": float(len(items)),
            }
        per_query[q.id] = stats
        err.print(f"[dim]{q.id} {q.query}[/dim]")

    mean = {
        v: {
            m: sum(per_query[q][v][m] for q in per_query) / len(per_query)
            for m in ("ndcg", "p5", "unjudged")
        }
        for v in VARIANTS
    }
    if as_json:
        typer.echo(json.dumps({"per_query": per_query, "mean": mean}, ensure_ascii=False, indent=2))
        return
    table = Table(title=f"nDCG@{K} / P@5（{len(per_query)} 条问句；标注者见 judgments.tsv）")
    table.add_column("问句", no_wrap=True)
    for v in VARIANTS:
        table.add_column(v, justify="right")
    names = {q.id: q.query for q in queries}
    for qid, stats in per_query.items():
        table.add_row(
            f"{qid} {names[qid][:12]}",
            *[f"{stats[v]['ndcg']:.2f}/{stats[v]['p5']:.1f}" for v in VARIANTS],
        )
    table.add_section()
    table.add_row(
        "[bold]平均[/bold]",
        *[f"[bold]{mean[v]['ndcg']:.3f}[/bold]/{mean[v]['p5']:.2f}" for v in VARIANTS],
    )
    table.add_row(
        "[dim]Top10 未标注[/dim]", *[f"[dim]{mean[v]['unjudged']:.1f}[/dim]" for v in VARIANTS]
    )
    console.print(table)
