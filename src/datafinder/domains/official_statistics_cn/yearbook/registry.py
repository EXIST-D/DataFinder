"""年鉴系列注册表（data/yearbooks.yaml）。"""

from __future__ import annotations

from dataclasses import dataclass

from datafinder.resources import load_package_yaml


@dataclass(frozen=True)
class Edition:
    """没有"每年一版"规律的资料（如普查）逐版列出：参考年、目录页所在目录、名称。"""

    year: int
    base: str
    label: str | None = None
    landing: str | None = None  # 给人看的入口页；默认是 base + indexch.htm


@dataclass(frozen=True)
class Series:
    id: str
    zh: str
    region: str
    publisher: str
    home: str
    years: tuple[int, int] | None = None
    base: str | None = None
    toc: tuple[str, ...] = ("left.htm",)
    crawl: bool = True
    type: str = "yearbook"  # yearbook 统计年鉴 | census 普查资料（同一套目录页模板）
    editions: tuple[Edition, ...] = ()
    topics: tuple[str, ...] = ()  # 整个系列固定的主题（普查资料按卷章难以推断主题）
    format: str = "zk"  # 目录页形态：zk 统计出版社模板 | tree 树形目录（上海）| list 只有各版次列表（广东、湖北）
    landing: str | None = None  # 版次入口页模板（含 {year}）；默认 base + indexch.htm
    list_urls: tuple[str, ...] = ()  # format=list：各版次列表页（可多页）
    follow: bool = False  # format=list：进入每个版次页收集附件（zip/xls/pdf）与在线浏览链接

    def edition_years(self, lo: int | None = None, hi: int | None = None) -> list[int]:
        if self.editions:
            return [e.year for e in self.editions if (lo or 0) <= e.year <= (hi or 9999)]
        if not self.years:
            return []
        a, b = self.years
        return list(range(max(a, lo or a), min(b, hi or b) + 1))

    def _edition(self, year: int) -> Edition | None:
        return next((e for e in self.editions if e.year == year), None)

    def base_for(self, year: int) -> str:
        assert self.base or self.editions
        e = self._edition(year)
        return e.base if e else (self.base or "").format(year=year)

    def label_for(self, year: int) -> str:
        e = self._edition(year)
        return (e.label if e and e.label else None) or str(year)

    def landing_for(self, year: int) -> str:
        e = self._edition(year)
        if e and e.landing:
            return e.landing
        if self.landing:
            return self.landing.format(year=year)
        return self.base_for(year) + "indexch.htm" if (self.base or self.editions) else self.home

    def toc_urls(self, year: int) -> list[str]:
        base = self.base_for(year)
        return [base + name.format(year=year) for name in self.toc]


def load_series() -> list[Series]:
    out = []
    for s in load_package_yaml("datafinder.domains.official_statistics_cn", "yearbooks.yaml")[
        "series"
    ]:
        years = tuple(s["years"]) if s.get("years") else None
        editions = tuple(Edition(**e) for e in s.get("editions", ()))
        out.append(
            Series(
                id=s["id"],
                zh=s["zh"],
                region=s["region"],
                publisher=s["publisher"],
                home=s["home"],
                years=years,  # type: ignore[arg-type]
                base=s.get("base"),
                toc=tuple(s.get("toc", ["left.htm"])),
                crawl=s.get("crawl", True)
                and bool(s.get("base") or editions or s.get("list_urls")),
                type=s.get("type", "yearbook"),
                editions=editions,
                topics=tuple(s.get("topics", ())),
                format=s.get("format", "zk"),
                landing=s.get("landing"),
                list_urls=tuple(s.get("list_urls", ())),
                follow=s.get("follow", False),
            )
        )
    return out


def get_series(series_id: str) -> Series:
    for s in load_series():
        if s.id == series_id:
            return s
    raise ValueError(f"未知年鉴系列 {series_id!r}，可选：{', '.join(s.id for s in load_series())}")
