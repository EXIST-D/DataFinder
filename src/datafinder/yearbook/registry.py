"""年鉴系列注册表（data/yearbooks.yaml）。"""

from __future__ import annotations

from dataclasses import dataclass

from ..resources import load_yaml


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

    def edition_years(self, lo: int | None = None, hi: int | None = None) -> list[int]:
        if not self.years:
            return []
        a, b = self.years
        return list(range(max(a, lo or a), min(b, hi or b) + 1))

    def toc_urls(self, year: int) -> list[str]:
        assert self.base
        base = self.base.format(year=year)
        return [base + name for name in self.toc]


def load_series() -> list[Series]:
    out = []
    for s in load_yaml("yearbooks.yaml")["series"]:
        years = tuple(s["years"]) if s.get("years") else None
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
                crawl=s.get("crawl", True) and bool(s.get("base")),
            )
        )
    return out


def get_series(series_id: str) -> Series:
    for s in load_series():
        if s.id == series_id:
            return s
    raise ValueError(f"未知年鉴系列 {series_id!r}，可选：{', '.join(s.id for s in load_series())}")
