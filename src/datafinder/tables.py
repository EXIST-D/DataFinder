"""取数结果的统一表格：整洁（tidy）长表 + 导出 CSV/Excel + 描述统计。

长表（每行一个 期别×地区×指标 的观测值）最适合直接进 pandas / R / Excel 透视表，
也最容易做描述统计。网页、CLI、MCP 共用这一种结构。
"""

from __future__ import annotations

import csv
import io
import re
import statistics
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import data_dir

COLUMNS = ["期别", "期别代码", "地区", "指标", "数值", "单位"]
_SAFE = re.compile(r"[^\w\u4e00-\u9fff-]+")


@dataclass
class Table:
    title: str
    rows: list[list[Any]]  # 每行对应 COLUMNS；数值列为 float（无法解析时为原字符串）
    meta: dict[str, Any] = field(default_factory=dict)  # 来源、链接、口径、取数时间…
    columns: list[str] = field(default_factory=lambda: list(COLUMNS))

    def __len__(self) -> int:
        return len(self.rows)

    def records(self) -> list[dict[str, Any]]:
        return [dict(zip(self.columns, r, strict=True)) for r in self.rows]

    def to_dict(self, *, max_rows: int | None = None) -> dict[str, Any]:
        rows = self.rows if max_rows is None else self.rows[:max_rows]
        return {
            "title": self.title,
            "columns": self.columns,
            "rows": rows,
            "row_count": len(self.rows),
            "truncated": max_rows is not None and len(self.rows) > max_rows,
            "meta": self.meta,
        }


def to_number(value: Any) -> float | str:
    """来源的数值常是字符串；能转就转成 float，不能转（如"—"）保持原样。"""
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, int | float):
        return float(value)
    text = str(value).strip().replace(",", "")
    try:
        return float(text)
    except ValueError:
        return str(value)


# ───────────── 导出 ─────────────


def exports_dir() -> Path:
    return data_dir() / "exports"


def _safe_name(text: str, limit: int = 40) -> str:
    return _SAFE.sub("_", text).strip("_")[:limit] or "table"


def default_path(table: Table, fmt: str) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return exports_dir() / f"{_safe_name(table.title)}-{stamp}.{fmt}"


def to_csv_text(table: Table) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(table.columns)
    w.writerows(table.rows)
    return buf.getvalue()


def save(table: Table, path: str | Path | None = None, fmt: str | None = None) -> Path:
    """保存为 csv（带 BOM，Excel 直接打开不乱码）或 xlsx（数据表 + 说明表）。"""
    target = Path(path).expanduser() if path else None
    fmt = (fmt or (target.suffix.lstrip(".").lower() if target else "csv")) or "csv"
    if fmt not in ("csv", "xlsx"):
        raise ValueError("只支持 csv 或 xlsx")
    target = target or default_path(table, fmt)
    if target.suffix.lower() != f".{fmt}":
        target = target.with_suffix(f".{fmt}")
    target.parent.mkdir(parents=True, exist_ok=True)
    if fmt == "csv":
        target.write_text(to_csv_text(table), encoding="utf-8-sig")
        return target

    from openpyxl import Workbook  # 只有导出 Excel 时才需要

    wb = Workbook()
    ws = wb.active
    ws.title = "数据"
    ws.append(table.columns)
    for row in table.rows:
        ws.append(row)
    ws.freeze_panes = "A2"
    widths = {"期别": 12, "期别代码": 12, "地区": 14, "指标": 36, "数值": 16, "单位": 10}
    for i, name in enumerate(table.columns, start=1):
        ws.column_dimensions[ws.cell(1, i).column_letter].width = widths.get(name, 14)
    note = wb.create_sheet("说明")
    note.column_dimensions["A"].width = 14
    note.column_dimensions["B"].width = 90
    note.append(["标题", table.title])
    for k, v in table.meta.items():
        note.append([k, v if isinstance(v, str | int | float) else str(v)])
    wb.save(target)
    return target


# ───────────── 描述统计 ─────────────


def _fmt(x: float | None, digits: int = 4) -> float | None:
    return None if x is None else round(x, digits)


def _is_year(code: str) -> bool:
    return bool(re.fullmatch(r"\d{4}YY", code))


def describe(table: Table) -> dict[str, Any]:
    """按 指标×地区 给出描述统计，并对最新一期做地区横向比较。

    只做稳妥的、不依赖假设的统计：个数、首末期、均值、中位数、标准差、变异系数、极值，
    以及（年度数据且首末值为正时）区间增长率和年均复合增长率。不做显著性检验。
    """
    cols = {c: i for i, c in enumerate(table.columns)}
    need = ("期别", "期别代码", "地区", "指标", "数值")
    if any(c not in cols for c in need):
        raise ValueError("描述统计需要整洁长表（期别、期别代码、地区、指标、数值 五列）")

    groups: dict[tuple[str, str], list[tuple[str, str, float]]] = {}
    units: dict[str, str] = {}
    skipped = 0
    for row in table.rows:
        value = row[cols["数值"]]
        if not isinstance(value, float):
            skipped += 1
            continue
        key = (str(row[cols["指标"]]), str(row[cols["地区"]]))
        groups.setdefault(key, []).append(
            (str(row[cols["期别代码"]]), str(row[cols["期别"]]), value)
        )
        if "单位" in cols and row[cols["单位"]]:
            units[key[0]] = str(row[cols["单位"]])

    series = []
    for (indicator, region), obs in groups.items():
        obs.sort(key=lambda o: o[0])
        values = [o[2] for o in obs]
        mean = statistics.fmean(values)
        sd = statistics.stdev(values) if len(values) > 1 else None
        first, last = obs[0], obs[-1]
        item: dict[str, Any] = {
            "indicator": indicator,
            "unit": units.get(indicator, ""),
            "region": region,
            "n": len(values),
            "first_period": first[1],
            "last_period": last[1],
            "first": first[2],
            "last": last[2],
            "mean": _fmt(mean),
            "median": _fmt(statistics.median(values)),
            "std": _fmt(sd),
            "cv": _fmt(sd / abs(mean)) if sd is not None and mean else None,
            "min": min(values),
            "max": max(values),
        }
        if first[2] > 0 and last[2] > 0 and len(values) > 1:
            item["total_growth"] = _fmt(last[2] / first[2] - 1)
            if _is_year(first[0]) and _is_year(last[0]):
                years = int(last[0][:4]) - int(first[0][:4])
                if years > 0:
                    item["cagr"] = _fmt((last[2] / first[2]) ** (1 / years) - 1)
        series.append(item)

    # 最新一期的地区横向比较（只比较同一指标、同一期别的地区）
    cross: list[dict[str, Any]] = []
    by_indicator: dict[str, list[tuple[str, str, float]]] = {}
    for s in series:
        by_indicator.setdefault(s["indicator"], []).append(
            (s["region"], s["last_period"], s["last"])
        )
    for indicator, items in by_indicator.items():
        if len(items) < 2:
            continue
        period = max({p for _, p, _ in items}, key=lambda p: sum(1 for _, q, _ in items if q == p))
        same = sorted(((r, v) for r, p, v in items if p == period), key=lambda x: -x[1])
        if len(same) < 2:
            continue
        vals = [v for _, v in same]
        cross.append(
            {
                "indicator": indicator,
                "period": period,
                "ranking": [
                    {"region": r, "value": v, "rank": i + 1} for i, (r, v) in enumerate(same)
                ],
                "mean": _fmt(statistics.fmean(vals)),
                "range_ratio": _fmt(vals[0] / vals[-1]) if vals[-1] > 0 else None,
            }
        )
    return {
        "title": table.title,
        "observations": len(table.rows) - skipped,
        "skipped_non_numeric": skipped,
        "series": series,
        "cross_section": cross,
        "note": "仅为描述性统计；统计数据常有口径调整与修订，跨年比较前请核对统计口径。",
    }


def describe_markdown(result: dict[str, Any]) -> str:
    """描述统计的 Markdown 摘要，给命令行和 AI 助手直接阅读。"""

    def num(x: Any) -> str:
        if x is None:
            return "—"
        return f"{x:,.2f}" if isinstance(x, float) and abs(x) >= 100 else f"{x:g}"

    def pct(x: Any) -> str:
        return "—" if x is None else f"{x * 100:.1f}%"

    lines = [f"## {result['title']} · 描述统计", ""]
    lines += [
        "| 指标 | 地区 | 期数 | 首期→末期 | 首值 | 末值 | 均值 | 中位数 | 变异系数 | 年均增速 |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for s in result["series"]:
        name = s["indicator"] + (
            f"（{s['unit']}）" if s["unit"] and s["unit"] not in s["indicator"] else ""
        )
        lines.append(
            f"| {name} | {s['region']} | {s['n']} | {s['first_period']}→{s['last_period']} "
            f"| {num(s['first'])} | {num(s['last'])} | {num(s['mean'])} | {num(s['median'])} "
            f"| {pct(s['cv'])} | {pct(s.get('cagr'))} |"
        )
    for c in result["cross_section"]:
        lines += ["", f"**{c['indicator']}**，{c['period']} 地区排名：", ""]
        lines.append(
            "；".join(f"{r['rank']}. {r['region']} {num(r['value'])}" for r in c["ranking"])
        )
    lines += ["", f"> {result['note']}"]
    return "\n".join(lines)


# ───────────── 产物清单与覆盖检查 ─────────────


def sha256_of(path: Path) -> str:
    import hashlib

    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def write_manifest(artifact: Path, **info: Any) -> Path:
    """在产物旁写 `<文件名>.manifest.json`：来源、请求参数、取数/下载时间、体积、sha256、许可。"""
    import json

    manifest = artifact.with_name(artifact.name + ".manifest.json")
    data = {
        "file": artifact.name,
        "size_bytes": artifact.stat().st_size,
        "sha256": sha256_of(artifact),
        "created_at": datetime.now().isoformat(timespec="seconds"),
        **info,
    }
    manifest.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    return manifest


def coverage(table: Table, *, periods: list[str] | None = None) -> dict[str, Any] | None:
    """取数后的覆盖检查：期望的 地区×指标×期别 单元里，实际取到有效数值的有多少、缺哪些。

    期望的地区与指标由适配器写在 table.meta["expected"]（用户请求的那些）；期别为 `periods`（如明确给了
    起止年），否则用"实际出现过的期别并集"——这样只能发现"某个地区/指标缺了别人有的期"，不会假装知道应有多少期。
    没有 expected 就不给结论（返回 None）。
    """
    expected = table.meta.get("expected")
    if not expected:
        return None
    cols = {c: i for i, c in enumerate(table.columns)}
    if any(c not in cols for c in ("期别代码", "地区", "指标", "数值")):
        return None
    got = {
        (r[cols["地区"]], r[cols["指标"]], r[cols["期别代码"]])
        for r in table.rows
        if isinstance(r[cols["数值"]], float)
    }
    period_basis = "explicit" if periods else "observed_union"
    per = periods or sorted({k[2] for k in got})
    cells = [(g, i, p) for g in expected["regions"] for i in expected["indicators"] for p in per]
    missing = [c for c in cells if c not in got]
    by = lambda idx: {  # noqa: E731
        k: sum(1 for m in missing if m[idx] == k) for k in {m[idx] for m in missing}
    }
    return {
        "period_basis": period_basis,
        "expected_cells": len(cells),
        "observed_cells": len(cells) - len(missing),
        "missing_cells": len(missing),
        "coverage_ratio": round((len(cells) - len(missing)) / len(cells), 4) if cells else None,
        "missing_by_region": by(0),
        "missing_by_indicator": by(1),
        "missing_by_period": by(2),
        "missing_sample": [{"region": g, "indicator": i, "period": p} for g, i, p in missing[:30]],
        "status": "complete"
        if cells and not missing
        else ("partial" if len(missing) < len(cells) else "none"),
    }
