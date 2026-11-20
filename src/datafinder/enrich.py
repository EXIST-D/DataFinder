"""数据集详情的"要点化"：地区、要点列表、所属门户、DOI 链接、准确的打开链接。

原则：只用上游给出的事实拼装，**不用 AI 编写**；推断出来的字段（如从标题/描述识别的地区）
必须标注依据，缺失就写"上游未提供"，不编造。
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

from .engine.dedup import normalize_doi
from .models import Dataset
from .query.plan import country_zh_name, find_locations, province_zh_name
from .sources.base import SourceSpec
from .util import normalize_url

_GLOBAL_WORDS = ("global", "worldwide", "全球", "世界范围", "各国")


@dataclass
class Enriched:
    region: str | None = None
    region_basis: str | None = None  # 来源所在地区 / 标题或描述中提到
    highlights: list[dict[str, str | None]] = field(default_factory=list)  # [{label, value, note}]
    portal: dict | None = None
    doi_url: str | None = None
    open_url: str = ""


def _first_sentence(text: str, limit: int = 90) -> str:
    """取第一句（保留句末标点）；太长就截断加省略号。"""
    cut = len(text)
    for sep, keep in (("。", 1), ("！", 1), ("？", 1), (". ", 1), ("! ", 1), ("? ", 1), ("\n", 0)):
        i = text.find(sep)
        if 0 < i < cut:
            cut = i + keep
    out = text[:cut].strip()
    return out if len(out) <= limit else out[: limit - 1].rstrip() + "…"


def _fmt_size(n: int | None) -> str | None:
    if not n:
        return None
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return None


def detect_region(d: Dataset, spec: SourceSpec | None) -> tuple[str | None, str | None]:
    """地区：先看来源本身所在地区（如国家统计局=中国），再看标题/描述里提到的地点。"""
    if spec and spec.options.get("country"):
        code = str(spec.options["country"])
        return country_zh_name(code) or code, "来源所在地区"
    text = f"{d.title}\n{(d.description or '')[:500]}"
    countries, regions = find_locations(text)
    if regions:
        names = [province_zh_name(r) or r for r in regions[:2]]
        country = country_zh_name(regions[0].split("-")[0]) or ""
        return f"{country}·{'、'.join(names)}", "标题或描述中提到"
    if countries:
        names = [country_zh_name(c) or c for c in countries]
        label = "、".join(names) if len(names) <= 3 else "多国"
        return label, "标题或描述中提到"
    if any(w in text.lower() for w in _GLOBAL_WORDS):
        return "全球/多国", "标题或描述中提到"
    return None, None


def access_summary(d: Dataset) -> str:
    kinds = {r.kind for r in d.resources}
    lic = (d.license or "").lower()
    if "restricted" in lic or "受限" in lic:
        return "受限访问，需向发布方申请"
    if "file" in kinds and "api" in kinds:
        return "可下载文件，也提供接口"
    if "file" in kinds:
        return f"可直接下载（{sum(1 for r in d.resources if r.kind == 'file')} 个文件）"
    if "api" in kinds:
        return "通过接口获取（见下方请求方式）"
    return "上游未列出文件或接口，请到原站查看"


def lookup_portal(conn: sqlite3.Connection, url: str) -> dict | None:
    """按链接的主机名找所属门户（得到机构类型）。DOI 等跳转链接找不到就返回 None。"""
    from .portals.browse import portal_dict

    key = normalize_url(url)
    host = key.split("/")[0].split("?")[0]
    if not host or host in ("doi.org", "dx.doi.org"):
        return None
    row = conn.execute(
        "SELECT * FROM portals WHERE norm_url = ? OR norm_url LIKE ? ORDER BY length(norm_url) LIMIT 1",
        (host, f"{host}/%"),
    ).fetchone()
    if row is None:
        return None
    pd = portal_dict(row)
    return {k: pd[k] for k in ("name", "url", "org_type", "official", "country")}


def enrich(
    d: Dataset,
    *,
    spec: SourceSpec | None = None,
    spec_titles: dict[str, str] | None = None,
    portal: dict | None = None,
) -> Enriched:
    region, basis = detect_region(d, spec)
    titles = spec_titles or {}
    where = "、".join(titles.get(s, s) for s in d.sources) or titles.get(d.source, d.source)
    doi = normalize_doi(d.doi)

    hl: list[dict[str, str | None]] = []

    def add(label: str, value: str | None, note: str | None = None) -> None:
        if value:
            hl.append({"label": label, "value": value, "note": note})

    if d.description:
        add("简介", _first_sentence(d.description))
    add("来源", where)
    add("地区", region or "上游未标明", f"依据：{basis}" if basis else None)
    pub = d.publisher
    if portal and portal.get("org_type") and pub:
        pub = f"{pub}（{portal['org_type']}）"
    elif portal and portal.get("org_type"):
        pub = portal["org_type"]
    add("发布方", pub)
    add("数据期", d.period)
    add("更新", d.updated)
    if d.formats:
        size = _fmt_size(d.size_bytes)
        add("格式", "、".join(d.formats[:6]) + (f"；约 {size}" if size else ""))
    add("许可", d.license or "许可未知（不代表可随意使用）")
    add("获取方式", access_summary(d))
    if doi:
        add("DOI", doi)
    return Enriched(
        region=region,
        region_basis=basis,
        highlights=hl,
        portal=portal,
        doi_url=f"https://doi.org/{doi}" if doi else None,
        open_url=d.landing_url or d.url,
    )
