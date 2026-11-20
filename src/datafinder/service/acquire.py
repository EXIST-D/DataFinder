"""获取：fetch（需要生成）与 download（可直接下载）。

**默认只预览，不执行**：必须带 confirm=True 才会联网取数/下载并写文件。搜索、检视、找数方案永远不触发获取。
获取方式三类：
- download  目录里登记过的文件直链 → 下载到受控目录（附清单：来源、时间、体积、sha256、许可）
- generate  通过适配器实时取数 → 整洁长表 → 导出 CSV/Excel，附描述统计与覆盖检查
- link      只有链接：给出链接与获取方式说明，不下载
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from datafinder.catalog import Entry
from datafinder.catalog.adapters import FetchParams, adapter_for
from datafinder.catalog.download import download_file
from datafinder.tables import coverage, exports_dir, write_manifest

from .common import open_catalog
from .errors import ServiceError, guard
from .export import table_result

USABILITY_ORDER = {"tabular": 0, "html_table": 1, "document": 2, "image": 3, None: 4}
CONFIRM_HINT = "确认无误后加 confirm=true（命令行加 --yes）才会真正执行"


def _entry(cat, resource_id: str) -> Entry:
    e = cat.get(resource_id)
    if e is None:
        raise ServiceError(
            f"目录里没有资源 {resource_id!r}",
            code="NOT_FOUND",
            next_action="用 search 找到 resource_id",
        )
    return e


def _confine(path: str | None, *, trusted: bool) -> str | None:
    """MCP 等不可信调用方只能写进导出目录；命令行（用户本人）可以指定任意路径。"""
    if path is None or trusted:
        return path
    p = Path(path).expanduser()
    p = p if p.is_absolute() else exports_dir() / p
    if exports_dir().resolve() not in p.resolve().parents:
        raise ServiceError(f"出于安全，只能写入导出目录：{exports_dir()}", code="INVALID_ARGUMENT")
    return str(p)


def _best_download(e: Entry, index: int | None):
    options = [d for d in e.distributions if d.delivery == "download" and d.url]
    if not options:
        raise ServiceError(
            "这个资源没有可直接下载的文件",
            code="DOWNLOAD_UNAVAILABLE",
            next_action="用 inspect 看 how_to_get：可能需要 fetch（生成）或只有链接",
        )
    if index is not None:
        if not 0 <= index < len(options):
            raise ServiceError(
                f"distribution_index 超出范围（0–{len(options) - 1}）", code="INVALID_ARGUMENT"
            )
        return options[index], options
    return min(options, key=lambda d: USABILITY_ORDER.get(d.usability, 4)), options


async def fetch(
    resource_id: str,
    *,
    regions: list[str] | None = None,
    indicators: list[str] | None = None,
    start: str | None = None,
    end: str | None = None,
    periods: int = 8,
    fmt: str | None = None,
    path: str | None = None,
    confirm: bool = False,
    describe: bool = True,
    trusted: bool = False,
) -> dict[str, Any]:
    """对"需要生成"的资源实时取数（国家统计局等）。不带 confirm 只返回将要做什么与还缺什么参数。"""
    with guard(), open_catalog() as cat:
        e = _entry(cat, resource_id)
        if e.delivery != "generate":
            raise ServiceError(
                f"{resource_id} 的获取方式是 {e.delivery}，不能 fetch"
                + ("；请用 download" if e.delivery == "download" else "；只有链接"),
                code="UNSUPPORTED",
                next_action="用 inspect 查看 how_to_get",
            )
        params = FetchParams(
            regions=regions or [],
            indicators=indicators or [],
            start=start,
            end=end,
            periods=periods,
        )
        plan = {
            "status": "plan",
            "resource_id": resource_id,
            "title": e.title,
            "delivery": "generate",
            "params": {
                "regions": params.regions,
                "indicators": params.indicators,
                "start": start,
                "end": end,
                "periods": periods,
            },
            "save_to": str(exports_dir()),
            "format": fmt or "csv",
            "confirm_required": True,
            "next_action": CONFIRM_HINT,
            "license": e.license or "许可未知（不代表可随意使用）",
        }
        if not confirm:
            return plan
        dest = _confine(path, trusted=trusted)
        table = await adapter_for(cat, e.source_id).fetch(cat, e, params)
        explicit = _explicit_periods(start, end)
        result = table_result(table, fmt=fmt, path=dest, stats=describe)
        result.update(
            status="done", resource_id=resource_id, coverage=coverage(table, periods=explicit)
        )
        write_manifest(
            Path(result["saved_to"]),
            resource_id=resource_id,
            source_id=e.source_id,
            title=e.title,
            source_url=table.meta.get("网址") or e.landing_url,
            license=e.license,
            request=plan["params"],
            row_count=len(table),
            kind="generated",
        )
        return result


def _explicit_periods(start: str | None, end: str | None) -> list[str] | None:
    """明确给了起止年（年度）时，期望的期别就是这段连续的年份；其他情况不假装知道。"""
    if (
        start
        and end
        and start.isdigit()
        and end.isdigit()
        and len(start) == len(end) == 4
        and start <= end
    ):
        return [f"{y}YY" for y in range(int(start), int(end) + 1)]
    return None


def download(
    resource_id: str,
    *,
    distribution_index: int | None = None,
    path: str | None = None,
    confirm: bool = False,
    trusted: bool = False,
) -> dict[str, Any]:
    """下载"可直接下载"的文件。不带 confirm 只返回将下载什么（格式、可用性、许可）。"""
    with guard(), open_catalog() as cat:
        e = _entry(cat, resource_id)
        dist, options = _best_download(e, distribution_index)
        dest_dir = Path(_confine(path, trusted=trusted) or exports_dir() / "downloads")
        plan = {
            "status": "plan",
            "resource_id": resource_id,
            "title": e.title,
            "delivery": "download",
            "url": dist.url,
            "file_format": dist.file_format,
            "usability": dist.usability,
            "other_options": [
                {"index": i, "url": d.url, "file_format": d.file_format, "usability": d.usability}
                for i, d in enumerate(options)
                if d is not dist
            ],
            "save_to": str(dest_dir),
            "confirm_required": True,
            "next_action": CONFIRM_HINT,
            "license": e.license or "许可未知（不代表可随意使用）",
            "warning": "图片文件不能直接做数据分析（需要 OCR）"
            if dist.usability == "image"
            else None,
        }
        if not confirm:
            return plan
        assert dist.url
        saved, size = download_file(dist.url, dest_dir, hint=resource_id)
        write_manifest(
            saved,
            resource_id=resource_id,
            source_id=e.source_id,
            title=e.title,
            source_url=dist.url,
            license=e.license,
            file_format=dist.file_format,
            usability=dist.usability,
            kind="downloaded",
        )
        return {
            **plan,
            "status": "done",
            "saved_to": str(saved),
            "size_bytes": size,
            "confirm_required": False,
            "next_action": None,
        }


async def get(resource_id: str, **kw: Any) -> dict[str, Any]:
    """按获取方式自动分流：download → 下载，generate → 取数，link → 给出链接。命令行 `datafinder get` 用它。"""
    with guard(), open_catalog() as cat:
        e = _entry(cat, resource_id)
        delivery = e.delivery
        links = [
            {"url": d.url, "note": d.note, "access_method": d.access_method}
            for d in e.distributions
            if d.url
        ]
        landing = e.landing_url
    if delivery == "download":
        return download(
            resource_id,
            distribution_index=kw.get("distribution_index"),
            path=kw.get("path"),
            confirm=kw.get("confirm", False),
            trusted=kw.get("trusted", False),
        )
    if delivery == "generate":
        return await fetch(
            resource_id, **{k: v for k, v in kw.items() if k != "distribution_index"}
        )
    return {
        "status": "link",
        "resource_id": resource_id,
        "delivery": "link",
        "landing_url": landing,
        "links": links,
        "note": "只有链接：请在浏览器打开；需要登录/申请/拒绝机器人的站点不会被自动访问",
    }
