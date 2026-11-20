"""门户库同步：下载 Dateno 注册表 → 打标 → 叠加精选 → 写入 SQLite。

数据来源：https://github.com/datenoio/dataportals-registry （代码 MIT，数据 CC BY 4.0）。
注册表只描述"门户/目录"，不含各门户里的数据集；其 `status` 为人工策展，不是实时探测。
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import httpx

from ..config import USER_AGENT, data_dir
from ..query.plan import country_zh_name, province_zh_name
from ..query.tokenize import token_text
from ..resources import load_yaml
from ..util import normalize_url
from .tag import industry_zh, load_config, tag_industries

REGISTRY_REPO = "datenoio/dataportals-registry"
PARQUET_PATH = "data/datasets/full.parquet"
MAX_DESC = 600

_COLUMNS = (
    "uid, id, name, link, description, catalog_type, access_mode, api, endpoints, "
    "software, owner, tags, topics, properties"
)


@dataclass
class Portal:
    id: str
    name: str
    url: str
    source: str
    name_zh: str | None = None
    org: str | None = None
    country: str | None = None
    country_en: str | None = None
    region: str | None = None
    catalog_type: str | None = None
    software: str | None = None
    api_url: str | None = None
    has_api: bool = False
    access: str | None = None
    industries: list[str] = field(default_factory=list)
    topics: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    description: str | None = None
    desc_zh: str | None = None
    is_national: bool = False
    featured: bool = False
    curated: bool = False

    @property
    def norm_url(self) -> str:
        return normalize_url(self.url)


@dataclass
class SyncResult:
    registry_ref: str
    total: int
    curated_applied: int
    curated_added: int
    curated_missing: list[str]
    seconds: float


# ───────────── 下载 ─────────────


def latest_registry_ref(client: httpx.Client) -> str:
    """优先固定到最新发布标签（可复现）；取不到就退回 main。"""
    try:
        resp = client.get(
            f"https://api.github.com/repos/{REGISTRY_REPO}/releases/latest", timeout=8
        )
        if resp.status_code == 200:
            return str(resp.json()["tag_name"])
    except (httpx.HTTPError, KeyError, ValueError):
        pass
    return "main"


def download_registry(
    *,
    force: bool = False,
    on_progress: Callable[[int, int | None], None] | None = None,
) -> tuple[Path, str]:
    cache = data_dir() / "cache"
    cache.mkdir(parents=True, exist_ok=True)
    headers = {"User-Agent": USER_AGENT}
    with httpx.Client(headers=headers, follow_redirects=True, timeout=60) as client:
        ref = latest_registry_ref(client)
        if ref == "main" and not force:
            # 版本查询失败（常见于 GitHub 匿名限流）：优先复用已缓存的带版本号快照
            tagged = sorted(cache.glob("registry-v*.parquet"), reverse=True)
            if tagged:
                return tagged[0], tagged[0].stem.removeprefix("registry-")
        dest = cache / f"registry-{ref}.parquet"
        fresh = dest.exists() and (ref != "main" or time.time() - dest.stat().st_mtime < 7 * 86400)
        if fresh and not force:
            return dest, ref
        tmp = dest.with_suffix(".part")
        errors: list[str] = []
        for url in mirror_urls(ref):
            try:
                with client.stream("GET", url, timeout=httpx.Timeout(60, read=30)) as resp:
                    resp.raise_for_status()
                    total = int(resp.headers.get("content-length", 0)) or None
                    done = 0
                    with tmp.open("wb") as fh:
                        for chunk in resp.iter_bytes(1 << 16):
                            fh.write(chunk)
                            done += len(chunk)
                            if on_progress:
                                on_progress(done, total)
                tmp.replace(dest)
                return dest, ref
            except httpx.HTTPError as exc:
                errors.append(f"{url}: {type(exc).__name__}")
                tmp.unlink(missing_ok=True)
        raise RuntimeError("门户库下载失败，已依次尝试：\n  " + "\n  ".join(errors))


def mirror_urls(ref: str) -> list[str]:
    """下载源（按顺序尝试）。内容与 GitHub 一致；可用 DATAFINDER_REGISTRY_URL 指定自己的源。"""
    urls = [
        f"https://raw.githubusercontent.com/{REGISTRY_REPO}/{ref}/{PARQUET_PATH}",
        f"https://cdn.jsdelivr.net/gh/{REGISTRY_REPO}@{ref}/{PARQUET_PATH}",
    ]
    override = os.environ.get("DATAFINDER_REGISTRY_URL")
    return [override, *urls] if override else urls


# ───────────── 读取与转换 ─────────────


def read_registry(path: Path) -> Iterator[dict]:
    import duckdb  # 仅 sync 时需要

    con = duckdb.connect()
    try:
        cur = con.execute(
            f"SELECT {_COLUMNS} FROM read_parquet(?) WHERE status = 'active'", [str(path)]
        )
        names = [d[0] for d in cur.description]
        for row in cur.fetchall():
            yield dict(zip(names, row, strict=True))
    finally:
        con.close()


def to_portal(rec: dict) -> Portal | None:
    name = (rec.get("name") or "").strip()
    link = (rec.get("link") or "").strip()
    if not name or not link:
        return None
    owner = rec.get("owner") or {}
    location = owner.get("location") or {}
    endpoints = rec.get("endpoints") or []
    software = (rec.get("software") or {}).get("name") or (rec.get("software") or {}).get("id")
    topics = [t["name"] for t in (rec.get("topics") or []) if t and t.get("name")]
    tags = [str(t) for t in (rec.get("tags") or [])]
    description = (rec.get("description") or "").strip()[:MAX_DESC] or None
    props = rec.get("properties") or {}
    portal = Portal(
        # 用 `id`（文件名主干，唯一）；`uid` 在 v1.27.0 快照里有 207 处重复，不能当主键
        id=str(rec.get("id") or rec.get("uid")),
        name=name,
        url=link,
        source="dateno",
        org=owner.get("name"),
        country=(location.get("country") or {}).get("id"),
        country_en=(location.get("country") or {}).get("name"),
        region=(location.get("subregion") or {}).get("id"),
        catalog_type=rec.get("catalog_type"),
        software=software,
        api_url=endpoints[0]["url"] if endpoints and endpoints[0].get("url") else None,
        has_api=bool(rec.get("api")),
        access=", ".join(rec.get("access_mode") or []) or None,
        topics=topics,
        tags=tags,
        description=description,
        is_national=bool(props.get("is_national")),
    )
    portal.industries = tag_industries(
        name=name, description=description or "", tags=tags, topics=topics
    )
    return portal


def apply_curated(portals: list[Portal], entries: list[dict]) -> tuple[int, int, list[str]]:
    """叠加精选：标注已有条目；`add: true` 的才新增。返回 (标注数, 新增数, 未匹配链接)。"""
    by_url = {p.norm_url: p for p in portals}
    applied = added = 0
    missing: list[str] = []
    for e in entries:
        key = normalize_url(e["link"])
        if e.get("add"):
            if key in by_url:  # 注册表已有，当作标注处理
                e = {k: v for k, v in e.items() if k != "add"}
            else:
                p = Portal(
                    id=f"curated:{key}",
                    name=e["name"],
                    url=e["link"],
                    source="curated",
                    country=e.get("country"),
                    catalog_type=e.get("catalog_type"),
                    is_national=bool(e.get("is_national")),
                )
                portals.append(p)
                by_url[key] = p
                added += 1
        p = by_url.get(key)
        if p is None:
            missing.append(e["link"])
            continue
        p.name_zh = e.get("name_zh") or p.name_zh
        p.desc_zh = e.get("desc_zh") or p.desc_zh
        p.featured = bool(e.get("featured", p.featured))
        p.curated = True
        if e.get("industries"):
            p.industries = list(e["industries"])  # 精选条目人工指定行业，覆盖自动打标
        if p.source == "dateno":
            p.source = "dateno+curated"
        applied += 1
    return applied, added, missing


def _hints(p: Portal, region_zh: str | None) -> str:
    """只用于检索的中文提示词（不展示）：让中文查询能命中英文条目。"""
    cfg = load_config()
    words: list[str] = []
    if p.country:
        words.append(country_zh_name(p.country) or "")
        words.append(p.country_en or "")
    words.append(region_zh or "")
    ct = cfg.catalog_types.get(p.catalog_type or "")
    if ct:
        words.append(ct["zh"])
        words.extend(ct.get("hints", []))
    for ind in p.industries:
        words.append(industry_zh(ind))
    return " ".join(w for w in words if w)


# ───────────── 写库 ─────────────


def write_db(conn: sqlite3.Connection, portals: list[Portal], meta: dict[str, str]) -> None:
    now = datetime.now(UTC).isoformat(timespec="seconds")
    with conn:
        conn.execute("DELETE FROM portals")
        conn.execute("DELETE FROM portals_fts")
        for rowid, p in enumerate(portals, start=1):
            region_zh = province_zh_name(p.region) if p.region else None
            hints = _hints(p, region_zh)
            description = " ".join(
                x for x in [p.description, p.desc_zh, " ".join(p.tags), p.org] if x
            )
            name_tokens = token_text(p.name)
            name_zh_tokens = token_text(p.name_zh or "")
            desc_tokens = token_text(description)
            hint_tokens = token_text(hints)
            all_tokens = " ".join([name_tokens, name_zh_tokens, desc_tokens, hint_tokens])
            conn.execute(
                "INSERT INTO portals(rowid, id, name, name_zh, url, norm_url, org, country,"
                " country_en, region, region_zh, catalog_type, software, api_url, has_api, access,"
                " industries, topics, description, desc_zh, is_national, featured, curated,"
                " source, tokens, imported_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    rowid,
                    p.id,
                    p.name,
                    p.name_zh,
                    p.url,
                    p.norm_url,
                    p.org,
                    p.country,
                    p.country_en,
                    p.region,
                    region_zh,
                    p.catalog_type,
                    p.software,
                    p.api_url,
                    int(p.has_api),
                    p.access,
                    json.dumps(p.industries, ensure_ascii=False),
                    json.dumps(p.topics, ensure_ascii=False),
                    p.description,
                    p.desc_zh,
                    int(p.is_national),
                    int(p.featured),
                    int(p.curated),
                    p.source,
                    all_tokens,
                    now,
                ),
            )
            conn.execute(
                "INSERT INTO portals_fts(rowid, name, name_zh, description, hints)"
                " VALUES (?,?,?,?,?)",
                (rowid, name_tokens, name_zh_tokens, desc_tokens, hint_tokens),
            )
        conn.execute("DELETE FROM meta")
        conn.executemany(
            "INSERT INTO meta(key, value) VALUES (?, ?)",
            [*meta.items(), ("synced_at", now)],
        )


def build_portals(records: Iterator[dict]) -> list[Portal]:
    seen: set[str] = set()
    portals: list[Portal] = []
    for rec in records:
        p = to_portal(rec)
        if p is None or p.norm_url in seen:  # 注册表 URL 重复的只留第一条
            continue
        seen.add(p.norm_url)
        portals.append(p)
    return portals


def sync(
    conn: sqlite3.Connection,
    *,
    force: bool = False,
    registry_path: Path | None = None,
    on_progress: Callable[[int, int | None], None] | None = None,
) -> SyncResult:
    started = time.time()
    if registry_path is None:
        registry_path, ref = download_registry(force=force, on_progress=on_progress)
    else:
        ref = registry_path.name
    portals = build_portals(read_registry(registry_path))
    entries = load_yaml("curated", "portals.yaml")
    applied, added, missing = apply_curated(portals, entries)
    write_db(
        conn,
        portals,
        {
            "registry_ref": ref,
            "registry_source": f"https://github.com/{REGISTRY_REPO} (CC BY 4.0)",
            "portal_count": str(len(portals)),
            "curated_applied": str(applied),
            "curated_added": str(added),
            "curated_missing": json.dumps(missing, ensure_ascii=False),
        },
    )
    return SyncResult(ref, len(portals), applied, added, missing, time.time() - started)
