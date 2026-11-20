"""检索请求与 SQL 条件构造。

分面之间 AND；同一分面多选 OR；每个分面的条件单独成块，算分面计数时可以"去掉自己这一块"。
两种模式：
- discovery（默认）：高召回。分类/时间等信息缺失（unknown）的资源也返回，并标注缺什么；
- strict：高精度。缺失、未验证一律不当作满足。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..query.tokenize import tokenize
from ..query.units import Unit, prefilter_terms
from . import kinds, regions, taxonomy

MODES = ("discovery", "strict")
REGION_MATCHES = ("intersects", "exact")
GEO_LEVELS = ("country", "province", "prefecture", "county")
# 来源结构决定或已验证的地域证据才算"确认"；标题推断不算
CONFIRMED_EVIDENCE = ("structure", "verified")


class InvalidFacet(ValueError):
    """请求里的主题/地域/类型等 id 不合法。"""


@dataclass
class SearchRequest:
    query: str = ""
    domain_ids: list[str] = field(default_factory=list)
    topic_ids: list[str] = field(default_factory=list)
    region_ids: list[str] = field(default_factory=list)
    region_match: str = "intersects"
    geo_granularity: str | None = None
    kinds: list[str] = field(default_factory=list)
    delivery: list[str] = field(default_factory=list)
    period_start: int | None = None
    period_end: int | None = None
    frequency: str | None = None
    source_ids: list[str] = field(default_factory=list)
    mode: str = "discovery"
    limit: int = 20
    offset: int = 0
    include_facets: bool = True
    group_versions: bool = True

    @property
    def has_filters(self) -> bool:
        """除关键词外是否还有结构化筛选条件。"""
        return bool(
            self.domain_ids
            or self.topic_ids
            or self.region_ids
            or self.kinds
            or self.delivery
            or self.source_ids
            or self.geo_granularity
            or self.frequency
            or self.period_start
            or self.period_end
        )

    def normalized(self) -> SearchRequest:
        """校验并把名称/别名/代码解析成规范 id；认不出的直接报错，不猜。"""
        if self.mode not in MODES:
            raise InvalidFacet(f"mode 只能是 {MODES}")
        if self.region_match not in REGION_MATCHES:
            raise InvalidFacet(f"region_match 只能是 {REGION_MATCHES}")
        if self.geo_granularity and self.geo_granularity not in GEO_LEVELS:
            raise InvalidFacet(f"geo_granularity 只能是 {GEO_LEVELS}")
        bad_kinds = [k for k in self.kinds if k not in kinds.kinds()]
        if bad_kinds:
            raise InvalidFacet(f"未知资源类型 {bad_kinds}，可选：{sorted(kinds.kinds())}")
        bad_delivery = [d for d in self.delivery if d not in ("download", "generate", "link")]
        if bad_delivery:
            raise InvalidFacet(f"delivery 只能是 download / generate / link，收到 {bad_delivery}")
        topic_ids, region_ids = [], []
        for t in self.topic_ids:
            tid = taxonomy.resolve(t)
            if not tid:
                raise InvalidFacet(f"未知主题 {t!r}（用 facets 查看合法的主题 id）")
            topic_ids.append(tid)
        for r in self.region_ids:
            rid = regions.resolve(r)
            if not rid:
                raise InvalidFacet(f"未知地域 {r!r}（可写 CN-HE、河北、130000 等）")
            region_ids.append(rid)
        if self.period_start and self.period_end and self.period_start > self.period_end:
            raise InvalidFacet("period_start 不能晚于 period_end")
        return SearchRequest(
            **{
                **self.__dict__,
                "topic_ids": topic_ids,
                "region_ids": region_ids,
                "limit": max(1, min(self.limit, 100)),
                "offset": max(0, self.offset),
            }
        )


def delivery_rank_sql() -> str:
    """资源最方便的获取方式（0 download / 1 generate / 2 link）；没有分发入口 = link。"""
    order = {"download": 0, "generate": 1, "link": 2}
    cases = " ".join(
        f"WHEN '{m}' THEN {order[v['delivery']]}" for m, v in kinds.access_methods().items()
    )
    return (
        f"COALESCE((SELECT MIN(CASE d.access_method {cases} ELSE 2 END) FROM distributions d"
        " WHERE d.resource_id = r.resource_id), 2)"
    )


DELIVERY_BY_RANK = {0: "download", 1: "generate", 2: "link"}


def fts_query(terms: list[str]) -> str:
    """关键词 → FTS5 查询：每个词变成"相邻二元组"短语（等价于子串匹配），词与词之间 OR。"""
    phrases = []
    for t in terms:
        toks = tokenize(t)
        if toks:
            phrases.append('"' + " ".join(toks) + '"')
    return " OR ".join(phrases)


def _marks(values: list) -> str:
    return ",".join("?" * len(values))


class Where:
    """把请求拆成各分面的 SQL 块。`sql(skip=...)` 返回去掉指定分面后的条件（供分面计数用）。"""

    def __init__(self, req: SearchRequest, units: list[Unit] | None = None) -> None:
        self.req = req
        self.blocks: dict[str, tuple[str, list]] = {}
        self._build(units or [])

    def _add(self, name: str, sql: str, args: list | None = None) -> None:
        self.blocks[name] = (sql, args or [])

    def _build(self, units: list[Unit]) -> None:
        req, strict = self.req, self.req.mode == "strict"
        wanted_kinds = req.kinds or kinds.searchable_kinds()
        self._add("kind", f"r.kind IN ({_marks(wanted_kinds)})", list(wanted_kinds))
        terms = prefilter_terms(units)
        match = fts_query(terms)
        if match:
            self._add(
                "text",
                "r.rowid IN (SELECT rowid FROM resources_fts WHERE resources_fts MATCH ?)",
                [match],
            )
        if req.source_ids:
            self._add("source", f"r.source_id IN ({_marks(req.source_ids)})", list(req.source_ids))
        if req.domain_ids:
            self._add(
                "domain",
                f"r.source_id IN (SELECT source_id FROM sources WHERE domain_id IN ({_marks(req.domain_ids)}))",
                list(req.domain_ids),
            )
        if req.topic_ids:
            expanded = list(
                dict.fromkeys(t for tid in req.topic_ids for t in (tid, *taxonomy.descendants(tid)))
            )
            self._add(
                "topic",
                "EXISTS (SELECT 1 FROM resource_topics rt WHERE rt.resource_id = r.resource_id"
                f" AND rt.topic_id IN ({_marks(expanded)}))",
                expanded,
            )
        if req.region_ids:
            self._add("region", *self._region_block(strict))
        if req.geo_granularity:
            self._add("granularity", *self._granularity_block(strict))
        if req.delivery:
            ranks = [r for r, name in DELIVERY_BY_RANK.items() if name in req.delivery]
            self._add("delivery", f"{delivery_rank_sql()} IN ({_marks(ranks)})", ranks)
        if req.period_start or req.period_end:
            lo, hi = req.period_start or -(10**6), req.period_end or 10**6
            known = "(r.period_start IS NOT NULL AND r.period_start <= ? AND (r.period_end IS NULL OR r.period_end >= ?))"
            self._add(
                "period", known if strict else f"({known} OR r.period_start IS NULL)", [hi, lo]
            )
        if req.frequency:
            eq = "r.frequency = ?"
            self._add(
                "frequency", eq if strict else f"({eq} OR r.frequency IS NULL)", [req.frequency]
            )

    def _region_block(self, strict: bool) -> tuple[str, list]:
        clauses, args = [], []
        exact = self.req.region_match == "exact"
        for rid in self.req.region_ids:
            # exact：资源明确标注为该地区本身。"含下级"（descendants）的资源是按下级逐个报告的数据，不算地区本身
            clauses.append(
                "(rr.region_id = ? AND rr.scope_mode != 'descendants')"
                if exact
                else "rr.region_id = ?"
            )
            args.append(rid)
            if not exact:
                anc = regions.ancestors(rid)
                if anc:  # 上级区域的"含下级"资源也覆盖本区域（如全国分省库覆盖河北）
                    clauses.append(
                        f"(rr.scope_mode = 'descendants' AND rr.region_id IN ({_marks(anc)}))"
                    )
                    args += anc
        sql = (
            "EXISTS (SELECT 1 FROM resource_regions rr WHERE rr.resource_id = r.resource_id"
            f" AND ({' OR '.join(clauses)}))"
        )
        if not strict:  # 地域没标注的资源在宽松模式下保留
            sql = f"({sql} OR NOT EXISTS (SELECT 1 FROM resource_regions rx WHERE rx.resource_id = r.resource_id))"
        return sql, args

    def _granularity_block(self, strict: bool) -> tuple[str, list]:
        g = self.req.geo_granularity
        base = "SELECT 1 FROM resource_regions rg WHERE rg.resource_id = r.resource_id AND rg.granularity = ?"
        if strict:  # 硬要求：只有来源结构决定/已验证的粒度才算满足，标题推断的不算
            return (
                f"EXISTS ({base} AND rg.evidence_status IN ({_marks(list(CONFIRMED_EVIDENCE))}))",
                [g, *CONFIRMED_EVIDENCE],
            )
        unknown = (
            "NOT EXISTS (SELECT 1 FROM resource_regions rn WHERE rn.resource_id = r.resource_id"
            " AND rn.granularity IS NOT NULL)"
        )
        return f"(EXISTS ({base}) OR {unknown})", [g]

    @property
    def match(self) -> str | None:
        """关键词的 FTS 查询串（没有关键词时为 None）。"""
        block = self.blocks.get("text")
        return block[1][0] if block else None

    def order_sql(self) -> tuple[str, list]:
        """纯分面浏览时的排序：已确认满足筛选条件的排前面，信息未知的排后面（再按类型、标题）。"""
        parts, args = [], []
        if self.req.geo_granularity:
            parts.append(
                "CASE WHEN EXISTS (SELECT 1 FROM resource_regions og WHERE og.resource_id = r.resource_id AND og.granularity = ?) THEN 0 ELSE 1 END"
            )
            args.append(self.req.geo_granularity)
        if self.req.period_start or self.req.period_end:
            parts.append("CASE WHEN r.period_start IS NOT NULL THEN 0 ELSE 1 END")
        if self.req.frequency:
            parts.append("CASE WHEN r.frequency IS NOT NULL THEN 0 ELSE 1 END")
        return "".join(f"{p}, " for p in parts), args

    def sql(self, skip: tuple[str, ...] = ()) -> tuple[str, list]:
        parts = [(s, a) for name, (s, a) in self.blocks.items() if name not in skip]
        return " AND ".join(s for s, _ in parts) or "1=1", [x for _, a in parts for x in a]
