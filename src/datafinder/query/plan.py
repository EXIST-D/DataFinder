"""查询计划：把一句话拆成 概念（可有多个中英说法）+ 过滤意图（地区/格式/年份/行业）。

输入  "中国各省近十年人口 GDP 城镇化率 CSV"
输出  concepts = [人口{人口|population|demographic}, GDP{gdp|gross domestic product}, 城镇化率{...}, 各省{...}]
      countries=[CN]  formats=[csv]  years=(2016, 2026)

只用规则，不依赖 LLM，可复现。可选 LLM 改写以后在此之上加。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from functools import lru_cache

from ..resources import load_yaml
from .lexicon import Lexicon, load_lexicon, segment
from .tokenize import has_cjk, normalize, tokenize

Phrase = tuple[str, ...]

_CN_DIGITS = {
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}
_RANGE = re.compile(r"((?:19|20)\d{2})\s*(?:年)?\s*(?:-|~|—|到|至|to)\s*((?:19|20)\d{2})\s*年?")
_RECENT = re.compile(r"近\s*([0-9]{1,2}|[一二两三四五六七八九十]{1,3})\s*年")
_SINGLE_YEAR = re.compile(r"(?<!\d)((?:19|20)\d{2})\s*年")

# 格式词 → 规范格式名（中文词按子串，英文词按单词边界）
_FORMATS = {
    "csv": "csv",
    "xlsx": "excel",
    "xls": "excel",
    "excel": "excel",
    "json": "json",
    "geojson": "geojson",
    "shp": "shapefile",
    "shapefile": "shapefile",
    "parquet": "parquet",
    "netcdf": "netcdf",
    "geotiff": "geotiff",
    "pdf": "pdf",
    "xml": "xml",
    "api": "api",
    "接口": "api",
    "表格": "table",
    "电子表格": "excel",
}


@dataclass(frozen=True)
class Concept:
    label: str
    alts: tuple[Phrase, ...]  # 每个 alt 是一个 token 短语；第 0 个是原词
    weak: bool = False
    industries: tuple[str, ...] = ()  # 该概念所属行业：门户带有这些行业标签也算命中


@dataclass
class QueryPlan:
    raw: str
    lang: str
    concepts: list[Concept] = field(default_factory=list)
    countries: list[str] = field(default_factory=list)
    regions: list[str] = field(default_factory=list)
    industries: list[str] = field(default_factory=list)
    formats: list[str] = field(default_factory=list)
    years: tuple[int, int] | None = None

    def fts_match(self) -> str | None:
        """生成 FTS5 MATCH 表达式：所有概念的所有说法用 OR 连接，每个说法是一个短语。"""
        seen: list[str] = []
        for c in self.concepts:
            for alt in c.alts:
                if alt:
                    phrase = '"' + " ".join(alt) + '"'
                    if phrase not in seen:
                        seen.append(phrase)
        return " OR ".join(seen) if seen else None


@dataclass(frozen=True)
class _Locations:
    # (needle, kind, code)，按长度降序，保证长词优先（印度尼西亚先于印度）
    zh: tuple[tuple[str, str, str], ...]
    en: tuple[tuple[re.Pattern[str], str], ...]
    country_zh: dict[str, str]
    province_zh: dict[str, str]


@lru_cache(maxsize=1)
def _locations() -> _Locations:
    raw = load_yaml("regions.yaml")
    zh: list[tuple[str, str, str]] = []
    en: list[tuple[re.Pattern[str], str]] = []
    country_zh: dict[str, str] = {}
    province_zh: dict[str, str] = {}
    for code, info in raw["provinces"].items():
        province_zh[code] = info["zh"]
        for name in [info["zh"], *info.get("aliases", [])]:
            zh.append((normalize(name), "region", code))
    for code, info in raw["countries"].items():
        country_zh[code] = info["zh"]
        for name in [info["zh"], *info.get("aliases", [])]:
            zh.append((normalize(name), "country", code))
        for name in info.get("en", []):
            en.append((re.compile(rf"(?<![a-z]){re.escape(name)}(?![a-z])"), code))
    zh.sort(key=lambda t: -len(t[0]))
    en.sort(key=lambda t: -len(t[0].pattern))
    return _Locations(tuple(zh), tuple(en), country_zh, province_zh)


def country_zh_name(code: str) -> str | None:
    return _locations().country_zh.get(code)


def province_zh_name(code: str) -> str | None:
    return _locations().province_zh.get(code)


@lru_cache(maxsize=1)
def _industry_keywords() -> dict[str, tuple[str, ...]]:
    """关键词 → 行业 id（只取查询里常见的中文关键词与单词关键词）。"""
    cfg = load_yaml("industries.yaml")
    out: dict[str, list[str]] = {}
    for ind in cfg["industries"]:
        for kw in ind["keywords"]:
            out.setdefault(normalize(kw), []).append(ind["id"])
    return {k: tuple(v) for k, v in out.items()}


def _parse_cn_int(text: str) -> int | None:
    if text.isdigit():
        return int(text)
    if text == "十":
        return 10
    if text.startswith("十") and len(text) == 2:
        return 10 + _CN_DIGITS.get(text[1], 0)
    if text.endswith("十") and len(text) == 2:
        return _CN_DIGITS.get(text[0], 0) * 10
    if len(text) == 1:
        return _CN_DIGITS.get(text)
    if len(text) == 3 and text[1] == "十":
        return _CN_DIGITS.get(text[0], 0) * 10 + _CN_DIGITS.get(text[2], 0)
    return None


def _extract_years(text: str, today: date) -> tuple[str, tuple[int, int] | None]:
    m = _RANGE.search(text)
    if m:
        a, b = sorted((int(m.group(1)), int(m.group(2))))
        return text.replace(m.group(0), " "), (a, b)
    m = _RECENT.search(text)
    if m:
        n = _parse_cn_int(m.group(1))
        if n:
            return text.replace(m.group(0), " "), (today.year - n, today.year)
    m = _SINGLE_YEAR.search(text)
    if m:
        y = int(m.group(1))
        return text.replace(m.group(0), " "), (y, y)
    return text, None


def _extract_formats(text: str) -> tuple[str, list[str]]:
    found: list[str] = []
    for word, fmt in sorted(_FORMATS.items(), key=lambda kv: -len(kv[0])):
        pattern = (
            re.compile(rf"(?<![a-z0-9]){re.escape(word)}(?![a-z0-9])")
            if word.isascii()
            else re.compile(re.escape(word))
        )
        if pattern.search(text):
            text = pattern.sub(" ", text)
            if fmt not in found:
                found.append(fmt)
    return text, found


def _extract_locations(text: str) -> tuple[str, list[str], list[str]]:
    loc = _locations()
    countries: list[str] = []
    regions: list[str] = []
    for needle, kind, code in loc.zh:
        if needle in text:
            text = text.replace(needle, " ")
            target = countries if kind == "country" else regions
            if code not in target:
                target.append(code)
    for pattern, code in loc.en:
        if pattern.search(text):
            text = pattern.sub(" ", text)
            if code not in countries:
                countries.append(code)
    # 识别到省份就隐含国家
    for r in regions:
        country = r.split("-")[0]
        if country not in countries:
            countries.append(country)
    return text, countries, regions


def parse(query: str, *, today: date | None = None, lex: Lexicon | None = None) -> QueryPlan:
    lex = lex or load_lexicon()
    today = today or date.today()
    text = normalize(query)
    plan = QueryPlan(raw=query, lang="zh" if has_cjk(query) else "en")

    text, plan.years = _extract_years(text, today)
    text, plan.formats = _extract_formats(text)
    text, plan.countries, plan.regions = _extract_locations(text)

    strong: list[Concept] = []
    weak: list[Concept] = []
    seen: set[str] = set()
    ind_kw = _industry_keywords()
    industries: list[str] = []

    for piece in segment(text, lex):
        label = piece.text
        if label in seen:
            continue
        seen.add(label)
        alts: list[Phrase] = []
        original = tuple(tokenize(label))
        if original:
            alts.append(original)
        if piece.kind in ("word", "weak"):
            for en in lex.zh2en.get(label, ()):
                phrase = tuple(tokenize(en))
                if phrase and phrase not in alts:
                    alts.append(phrase)
        if not alts:
            continue
        concept_inds: list[str] = []
        for key in {label, *lex.zh2en.get(label, ())}:
            for ind in ind_kw.get(normalize(key), ()):
                if ind not in concept_inds:
                    concept_inds.append(ind)
        concept = Concept(
            label=label,
            alts=tuple(alts),
            weak=piece.kind == "weak",
            industries=tuple(sorted(concept_inds)),
        )
        (weak if concept.weak else strong).append(concept)
        for ind in concept_inds:
            if ind not in industries:
                industries.append(ind)

    plan.concepts = strong or weak  # 有实义词时丢弃泛化词
    plan.industries = industries
    return plan
