"""省级统计查询平台（河北试点）：指标目录 + 年度数据面板入口。

河北统计局把"数据查询"做成了一套 dsfa 平台：

- **指标查询**：约 1,000 个节点的指标树（每个叶子有编码、单位、频度、口径、来源），
  页面在 `/dsfa/vue/index.html#/MacroQuery/3`。平台只提供指标**元数据**接口，
  没有公开的"按指标取数"接口——数值要在站点上按需查询，所以这里只登记指标卡片与入口。
- **数据查询**：2021 年起的年度数据面板（`sidenav.html?year=…`），是交互式图表页，
  同样没有可枚举的数据接口，按"一个资源、多个年度入口"登记。

因此本适配器只做 discover（登记元数据），不实现 fetch：资源都只有入口链接，统一跳转到站点查询。
新增省份时复制本模块，改 `SOURCE_ID` / `HOST` / `REGION` / `PANEL` 即可。
"""

from __future__ import annotations

from typing import Any

from datafinder.catalog.adapters import (
    CAP_DISCOVER,
    Adapter,
    AdapterError,
    DiscoverOptions,
    DiscoverResult,
    SourceInfo,
)
from datafinder.catalog.entities import Distribution, Entry, RegionLink
from datafinder.catalog.repository import Catalog
from datafinder.catalog.rules import Target
from datafinder.engine.http import Http

from .. import DOMAIN_ID
from ..classify import topics_for

SOURCE_ID = "province.hebei"
HOST = "tjj.hebei.gov.cn"
INDICATOR_TREE = f"https://{HOST}/dsfa/dsbi/base/indicator/tree"
INDICATOR_PAGE = f"https://{HOST}/dsfa/vue/index.html#/MacroQuery/3"
DATA_PAGE = f"https://{HOST}/hbstjj/sj/sjcx/sjcx"
PANEL = (
    f"http://{HOST}/dsfa/dsbi/customPage/hb_client/GXCX/views/sidenav.html?index=2&year={{year}}"
)
PANEL_YEARS = (2021, 2026)  # 平台实际提供 2021–2026 六个年度面板
REGION = "CN-HE"
INTERVAL = 1.5  # 同一站点相邻请求的最小间隔（秒）
ROOT_LABELS = {"统计指标"}  # 平台把全部指标挂在这个根节点下，它只是栏目名，不进路径
# 平台的"指标频度"文字 → 目录里的频度取值（与 NBS 保持一致）
FREQUENCY = {
    "年度": "annual",
    "年": "annual",
    "季度": "quarterly",
    "季": "quarterly",
    "月度": "monthly",
    "月": "monthly",
}


def _region() -> list[RegionLink]:
    return [RegionLink(REGION, "covers", None, "exact", "structure")]


def _entry(kind: str, key: str, title: str, *, parent: str | None = None, **kw: Any) -> Entry:
    return Entry(
        SOURCE_ID,
        kind,
        key,
        title,
        parent_id=parent,
        verification_level="source_asserted",
        regions=_region(),
        **kw,
    )


class HebeiQueryAdapter(Adapter):
    id = "hebei_query"
    domain_id = DOMAIN_ID
    capabilities = frozenset({CAP_DISCOVER})

    def sources(self) -> list[SourceInfo]:
        return [
            SourceInfo(
                SOURCE_ID,
                "河北省统计数据查询（指标查询 / 年度数据）",
                "河北省统计局",
                DATA_PAGE,
            )
        ]

    async def discover(self, cat: Catalog, opts: DiscoverOptions) -> DiscoverResult:
        """登记两个平台入口 + 指标树里的全部指标（只存元数据，不存数值）。"""
        self.register_sources(cat)
        run = cat.start_run(self.id, "discover")
        result = DiscoverResult("done")

        query_db = _entry(
            "database",
            "indicators",
            "河北省指标查询",
            landing_url=INDICATOR_PAGE,
            distributions=[Distribution("page", INDICATOR_PAGE)],
            extra={
                "publisher": "河北省统计局",
                "platform": "dsfa",
                "note": "指标元数据（口径/编码/单位）；数值需在站点查询",
            },
        )
        data_db = _entry(
            "database",
            "panels",
            "河北省统计数据查询（年度面板）",
            landing_url=DATA_PAGE,
            distributions=[
                Distribution("page", PANEL.format(year=y))
                for y in range(PANEL_YEARS[1], PANEL_YEARS[0] - 1, -1)
            ],
            extra={
                "publisher": "河北省统计局",
                "platform": "dsfa",
                "years": list(range(PANEL_YEARS[0], PANEL_YEARS[1] + 1)),
            },
        )
        entries: list[Entry] = [query_db, data_db]

        http = Http(min_interval={HOST: INTERVAL}, default_interval=INTERVAL)
        try:
            tree = await self._tree(http)
        except AdapterError as exc:
            result.stopped.append(f"河北省指标查询：{exc}")
            opts.say(f"指标树取不到：{exc}（只登记两个平台入口）")
            cat.upsert(entries)
            cat.finish_run(run, "ok", seen=0, changed=len(entries), message=str(exc))
            return result
        finally:
            await http.aclose()
        result.requests += 1

        db_id = query_db.resource_id
        roots = [
            child
            for node in tree
            if node.get("NAME") in ROOT_LABELS
            for child in node.get("children") or []
        ]
        count = 0
        for node, path in _walk(roots or tree):
            title = _text(node, "NAME", "name", "MC")
            code = _text(node, "ID", "id")
            if not title or not code:  # 没有编码的是空栏目，不是指标
                continue
            count += 1
            entries.append(
                _entry(
                    "indicator",
                    code,
                    title,
                    parent=db_id,
                    path=" / ".join(path),
                    unit=_text(node, "DW_TEXT"),
                    frequency=FREQUENCY.get(_text(node, "ZBPD_TEXT", "ZBPD")[:2].rstrip("度")),
                    landing_url=INDICATOR_PAGE,
                    topics=topics_for(
                        Target(top=path[0] if path else title, path=list(path), title=title)
                    ),
                    distributions=[Distribution("page", INDICATOR_PAGE, note="指标口径与查询入口")],
                    extra={
                        "code": _text(node, "ID", "id"),
                        "frequency": _text(node, "ZBPD_TEXT"),
                        "scope": _text(node, "FDQSJJB_TEXT"),
                        "source": _text(node, "ZBCC_TEXT"),
                        "category": _text(node, "TJZBZL1_TEXT"),
                        "latest_year": _text(node, "YXQSNF_TEXT"),
                        "platform": "dsfa",
                    },
                )
            )

        cat.upsert(entries)
        result.seen = len(entries)
        result.added = len(entries)
        cat.finish_run(run, "ok", seen=result.seen, changed=result.added, message="指标元数据")
        opts.say(f"登记 {count} 个指标（只存名称/口径/单位/入口，不存数值）")
        return result

    async def _tree(self, http: Http) -> list[dict[str, Any]]:
        """取指标树。平台用 POST + 空 body 返回 JSON；用 GET 会报"服务器异常"。"""
        try:
            payload = await http.post_json(INDICATOR_TREE)
        except Exception as exc:  # SourceError / ShapeError
            raise AdapterError(str(exc)) from exc
        if not isinstance(payload, dict) or not payload.get("success"):
            message = payload.get("message") if isinstance(payload, dict) else "响应结构异常"
            raise AdapterError(f"指标树接口报错：{message}")
        data = payload.get("data")
        if not isinstance(data, list) or not data:
            raise AdapterError("指标树结构异常：data 不是非空列表（平台可能改版）")
        return data


def _text(node: dict[str, Any], *keys: str) -> str:
    for k in keys:
        v = node.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


def _walk(nodes: list[dict[str, Any]], path: tuple[str, ...] = ()):
    """深度优先：产出 (叶子节点, 所属目录链)。目录链不含指标自己（它就是标题）。"""
    for node in nodes:
        name = _text(node, "NAME", "name", "MC")
        kids = node.get("children") or []
        if kids:
            yield from _walk(kids, (*path, name) if name else path)
        else:
            yield node, path
