"""取数结果的保存、预览与读回。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..tables import COLUMNS, Table, describe, describe_markdown, exports_dir, save, to_number
from .errors import ServiceError

PREVIEW_ROWS = 40


def table_result(
    table: Table, *, fmt: str | None = None, path: str | None = None, stats: bool = True
) -> dict:
    """保存并整理成给助手/命令行展示的结果。"""
    saved = save(table, path, fmt)
    out: dict[str, Any] = {
        "saved_to": str(saved),
        "format": saved.suffix.lstrip("."),
        **table.to_dict(max_rows=PREVIEW_ROWS),
    }
    if stats and table.rows:
        d = describe(table)
        out["describe"] = d
        out["describe_markdown"] = describe_markdown(d)
    return out


def load_table(path: str | Path, *, anywhere: bool = False) -> Table:
    """读回导出的 CSV/xlsx（整洁长表）。anywhere=False 时只允许 exports 目录内的文件。"""
    p = Path(path).expanduser()
    if not p.is_absolute():
        p = exports_dir() / p
    p = p.resolve()
    if not anywhere and exports_dir().resolve() not in p.parents:
        raise ServiceError(f"出于安全，只能读取导出目录内的文件：{exports_dir()}")
    if not p.is_file():
        raise ServiceError(f"文件不存在：{p}")
    rows: list[list[Any]] = []
    if p.suffix.lower() == ".csv":
        import csv

        with p.open(encoding="utf-8-sig", newline="") as f:
            reader = csv.reader(f)
            header = next(reader, [])
            for r in reader:
                rows.append(r)
    elif p.suffix.lower() == ".xlsx":
        from openpyxl import load_workbook

        ws = load_workbook(p, read_only=True, data_only=True)["数据"]
        it = ws.iter_rows(values_only=True)
        header = list(next(it, []))
        rows = [list(r) for r in it]
    else:
        raise ServiceError("只支持 .csv / .xlsx")
    if [str(h) for h in header[:5]] != COLUMNS[:5]:
        raise ServiceError(
            "不是 DataFinder 导出的整洁长表（需要 期别、期别代码、地区、指标、数值 等列）"
        )
    fixed = [[*r[:4], to_number(r[4]), *(r[5:6] or [""])] for r in rows if len(r) >= 5]
    return Table(title=p.stem, rows=fixed, meta={"来源文件": str(p)}, columns=list(header[:6]))
