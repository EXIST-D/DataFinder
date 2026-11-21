"""网页统计表 → 整洁长表（期别、地区、指标、数值、单位）。

统计年鉴、普查资料常把表格放成"从 Excel 发布的网页"：标题占一整行，其后是多级表头（行/列合并单元格）、
"（万吨）"这样的单位行、数据行、"注：…"脚注。本模块做通用的、可验证的解析：

1. 把 `<table>` 展开成规则网格（rowspan/colspan 都展开），选单元格最多的那张表；
2. 找标题、单位、表头行、数据行（第一个含数值的行起）、脚注（"注/资料来源"起，之后忽略）；
3. 左侧连续的非数值列是**行标签**；其余列的多级表头拼成**列路径**；
4. 行标签多数是地区名 → 标签就是"地区"；行标签是年份 → 标签就是"期别"；否则行标签并入"指标"；
5. **一个数值单元格 = 一行输出**：数值取页面上印出的文字（去掉千分位空格），印不出来才用 Excel 的 `x:num`；
   "—""…"等缺省记号不产生行。`meta["解析"]` 记录数值单元格数与输出行数，调用方可以核对"没有丢、没有造"。

只做结构还原，不做任何推断补全：识别不了表头/标签时降低可信度并写进 `meta["解析"]["提示"]`，而不是硬凑。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from html.parser import HTMLParser

from ..tables import Table
from . import regions
from .adapters import AdapterError

_CJK = r"\u4e00-\u9fff（）()、，：；"
_GAP = re.compile(rf"(?<=[{_CJK}])\s+(?=[{_CJK}])")
_SPACES = re.compile(r"[\s\u3000\xa0]+")
_NA = re.compile(
    r"[\s—–\-－─━…\.·*＊/]*"
)  # 缺省记号：只由破折号、省略号、星号、斜线组成（"—""——""…""..."）
_NUM = re.compile(r"^[+-]?\d+(?:\.\d+)?$")
_YEAR = re.compile(r"(?<!\d)((?:19|20)\d{2})(?!\d)")
_BARE_YEAR = re.compile(r"^((?:19|20)\d{2})\s*(?:年底|年末|年度|年)?$")
_TITLE_NO = re.compile(r"^(?:附录\s*)?(?:表\s*)?\d+(?:[-.．]\d+)+\s*[、.：:]?\s*[a-zA-Z]?\s*")
_UNIT_LINE = re.compile(r"^(?:计量)?单位\s*[:：]\s*(.+)$")
_PAREN_ONLY = re.compile(r"^[(（]\s*([^()（）]+?)\s*[)）]$")
# 表头里的年份是"分类"而不是观测期的表（按开业/成立/建成/出生年份分组等）：此时年份留在列名里
_YEAR_AS_CATEGORY = re.compile(
    r"按.{0,14}(时间|年份|年代|年度|年龄)分组|开业|成立时间|竣工时间|建成年代|出生年份"
)
# 表头名末尾括号里的计量单位："工业总产值（亿元）" → 名称"工业总产值"、单位"亿元"（只认像单位的括号，避免吃掉"（续）"之类）
_TAIL_UNIT = re.compile(
    r"^(.*?)[（(]([^()（）]*(?:元|%|百分|人|件|点|吨|公顷|户|个|万|亿|千|公里|平方|立方|度|瓦|辆|套|间|张|床|公斤|米|升|‰)[^()（）]*)[)）]$"
)
_RANGE = re.compile(r"[～~—–至-]\s*(?:19|20)\d{2}|(?:19|20)\d{2}\s*[～~—–至-]")
_CONTINUES = ("及以前", "以前", "以后", "及以后", "以来", "年代", "之前", "之后")
_TOTALS = {"总计", "合计", "全部", "总和", "全市", "全省", "全区"}
_NATION = {"全国", "中国", "全国总计"}


@dataclass(frozen=True)
class Cell:
    text: str
    num: float | None  # Excel 发布页的 x:num（单元格里存的数值）；没有就是 None
    origin: tuple[int, int]  # 合并单元格的左上角，用来区分"同一格被展开多次"
    indent: int = 0  # 文字前的缩进空格数（统计表用缩进表示"其中"层级）


def clean(text: str) -> str:
    """空白归一；汉字之间的排版空格去掉（"地  区"→"地区"），拉丁字母/数字之间的保留。"""
    return _GAP.sub("", _SPACES.sub(" ", text)).strip()


def parse_number(text: str) -> float | None:
    """页面上印出的数值文字 → 浮点；缺省记号与非数值返回 None。"""
    s = clean(text).replace(",", "").replace("，", "").replace(" ", "")
    s = s.replace("％", "").replace("%", "").replace("＋", "+").replace("－", "-")
    if not _NUM.match(s):
        return None
    return float(s)


class _Tables(HTMLParser):
    """收集所有表格的行与单元格（含 rowspan/colspan/x:num）。嵌套表格各自独立收集。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.done: list[list[list[tuple[dict, str]]]] = []
        self._stack: list[list[list[tuple[dict, str]]]] = []
        self._cell: tuple[dict, list[str]] | None = None
        self._skip = 0  # script/style 里的文字不是表格内容

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("script", "style"):
            self._skip += 1
        elif tag == "table":
            self._stack.append([])
        elif tag == "tr" and self._stack:
            self._stack[-1].append([])
        elif tag in ("td", "th") and self._stack:
            self._close_cell()
            if not self._stack[-1]:
                self._stack[-1].append([])
            self._cell = ({k: (v or "") for k, v in attrs}, [])
        elif tag == "br" and self._cell:
            self._cell[1].append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style"):
            self._skip = max(0, self._skip - 1)
        elif tag in ("td", "th"):
            self._close_cell()
        elif tag == "table" and self._stack:
            self._close_cell()
            self.done.append(self._stack.pop())

    def handle_data(self, data: str) -> None:
        if self._cell is not None and not self._skip:
            self._cell[1].append(data)

    def _close_cell(self) -> None:
        if self._cell is not None and self._stack and self._stack[-1]:
            attrs, parts = self._cell
            self._stack[-1][-1].append((attrs, "".join(parts)))
        self._cell = None


def _span(attrs: dict, name: str) -> int:
    try:
        return max(1, min(int(re.sub(r"\D", "", attrs.get(name, "1")) or 1), 200))
    except ValueError:
        return 1


def parse_grid(html: str) -> list[list[Cell]]:
    """选单元格最多的表格，把 rowspan/colspan 展开成规则网格（每行等宽，缺格补空单元格）。"""
    p = _Tables()
    p.feed(html)
    tables = [t for t in p.done if any(t)]
    if not tables:
        raise AdapterError("页面里没有找到表格")
    table = max(tables, key=lambda t: sum(len(r) for r in t))
    occupied: dict[tuple[int, int], Cell] = {}
    grid: list[list[Cell]] = []
    width = 0
    for r, row in enumerate(table):
        c = 0
        out: dict[int, Cell] = {}
        for attrs, raw in row:
            while (r, c) in occupied:
                out[c] = occupied.pop((r, c))
                c += 1
            num = None
            if attrs.get("x:num"):
                try:
                    num = float(attrs["x:num"])
                except ValueError:
                    num = None
            lead = len(raw) - len(raw.lstrip(" \xa0\u3000\t\r\n"))
            cell = Cell(clean(raw), num, (r, c), lead)
            for dr in range(_span(attrs, "rowspan")):
                for dc in range(_span(attrs, "colspan")):
                    if dr == 0:
                        out[c + dc] = cell
                    else:
                        occupied[(r + dr, c + dc)] = cell
            c += _span(attrs, "colspan")
        while (r, c) in occupied:
            out[c] = occupied.pop((r, c))
            c += 1
        width = max(width, max(out, default=-1) + 1)
        grid.append([out.get(i, Cell("", None, (r, i))) for i in range(max(out, default=-1) + 1)])
    return [
        row + [Cell("", None, (i, j)) for j in range(len(row), width)] for i, row in enumerate(grid)
    ]


_FRAME = re.compile(r"<i?frame\b[^>]*?\bsrc\s*=\s*[\"']?([^\"'\s>]+)", re.I)


def has_table(html: str) -> bool:
    return "<table" in html.lower()


# ── JS 注入的表格网格（浙江统计年鉴查看器）──
# 页面里没有 <table>，数据以 `var data = {"data":[[...]], "mergeCells":[...], "fixedRowsTop":2}` 注入，
# 由脚本画成表格。这里按 mergeCells 的 rowspan/colspan 还原成等价的 HTML 表，再交给上面的流程。
_GRID_VAR = re.compile(r"var\s+data\s*=\s*(\{.*?\})\s*;\s*(?:\$\(|</script>)", re.S)


def grid_html(html: str) -> str | None:
    """把内嵌的表格网格还原成 HTML 表；不是这种页面就返回 None。"""
    import html as _html
    import json

    m = _GRID_VAR.search(html)
    if not m:
        return None
    try:
        payload = json.loads(m.group(1))
    except ValueError:
        return None
    rows = payload.get("data")
    if not isinstance(rows, list) or not rows or not all(isinstance(r, list) for r in rows):
        return None
    spans = {
        (int(c["row"]), int(c["col"])): (int(c.get("rowspan", 1)), int(c.get("colspan", 1)))
        for c in payload.get("mergeCells") or []
        if isinstance(c, dict) and "row" in c and "col" in c
    }
    covered: set[tuple[int, int]] = set()
    width = max(len(r) for r in rows)
    title = str(payload.get("templetname") or "").strip()
    parts = ["<table>"]
    if title:
        parts.append(f'<tr><td colspan="{width}">{_html.escape(title)}</td></tr>')
    for r, row in enumerate(rows):
        parts.append("<tr>")
        for c, value in enumerate(row):
            if (r, c) in covered:
                continue
            rs, cs = spans.get((r, c), (1, 1))
            for dr in range(rs):
                for dc in range(cs):
                    if dr or dc:
                        covered.add((r + dr, c + dc))
            attrs = ""
            if rs > 1:
                attrs += f' rowspan="{rs}"'
            if cs > 1:
                attrs += f' colspan="{cs}"'
            parts.append(f"<td{attrs}>{_html.escape(str(value or ''))}</td>")
        parts.append("</tr>")
    parts.append("</table>")
    return "".join(parts)


def frame_sources(html: str, base_url: str) -> list[str]:
    """框架页（`<frameset>`/`<iframe>`）里各个子页面的绝对地址；表格常在其中一个框架里。"""
    from urllib.parse import urljoin

    return [urljoin(base_url, m.replace("\\", "/")) for m in _FRAME.findall(html)]


Layout = "str | tuple[str, list[Layout]]"  # 网页文本；或 ("rows"|"cols", 子布局们)


class _Frameset(HTMLParser):
    """框架页的嵌套结构：`<frameset rows=…>`（上下排）/`<frameset cols=…>`（左右排）里套 `<frame src>`。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root: tuple[str, list] | None = None
        self._stack: list[tuple[str, list]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k: v or "" for k, v in attrs}
        if tag == "frameset":
            n_rows, n_cols = a.get("rows", "").count(",") + 1, a.get("cols", "").count(",") + 1
            # rows="*" cols="295,*" 这种两个属性都写的：看哪个真的分成了多份
            self._stack.append(("cols" if n_cols > 1 and n_rows == 1 else "rows", []))
        elif tag in ("frame", "iframe") and a.get("src") and self._stack:
            self._stack[-1][1].append(a["src"])
        elif tag == "iframe" and a.get("src"):
            self.root = self.root or ("rows", [])
            self.root[1].append(a["src"])

    def handle_endtag(self, tag: str) -> None:
        if tag == "frameset" and self._stack:
            node = self._stack.pop()
            if self._stack:
                self._stack[-1][1].append(node)
            elif self.root is None or len(layout_urls(node)) > len(layout_urls(self.root)):
                self.root = node  # 页面末尾常有多余的空 <frameset></frameset>：取框架最多的那棵


def frame_layout(html: str, base_url: str) -> tuple[str, list] | None:
    """框架页 → 嵌套布局（叶子是子页面的绝对地址）；不是框架页返回 None。"""
    from urllib.parse import urljoin

    p = _Frameset()
    p.feed(html)
    if p.root is None:
        return None

    def absolute(node):
        kind, kids = node
        return (
            kind,
            [
                absolute(k) if isinstance(k, tuple) else urljoin(base_url, k.replace("\\", "/"))
                for k in kids
            ],
        )

    return absolute(p.root)


def layout_urls(node) -> list[str]:
    out: list[str] = []
    for k in node[1]:
        out += layout_urls(k) if isinstance(k, tuple) else [k]
    return list(dict.fromkeys(out))


def _shifted(g: list[list[Cell]], dr: int, dc: int) -> list[list[Cell]]:
    return [
        [Cell(c.text, c.num, (c.origin[0] + dr, c.origin[1] + dc), c.indent) for c in row]
        for row in g
    ]


def stack_grids(grids: list[list[list[Cell]]]) -> list[list[Cell]]:
    """上下排布的几个表（上面是标题与表头、下面是数据）纵向拼成一张表。"""
    width = max(len(g[0]) for g in grids if g)
    out: list[list[Cell]] = []
    for g in grids:
        # 合并单元格靠 origin 认"同一格"：平移整个框架的行号，保持各格的身份不变
        for row in _shifted(g, len(out), 0):
            r = len(out)
            out.append(row + [Cell("", None, (r, j)) for j in range(len(row), width)])
    return out


def hstack_grids(grids: list[list[list[Cell]]]) -> list[list[Cell]]:
    """左右排布的几个表（左边是行标签、右边是数据）按行对齐，横向拼成一张表。行数不一致时短的补空行。"""
    height = max(len(g) for g in grids)
    out: list[list[Cell]] = [[] for _ in range(height)]
    off = 0
    for g in grids:
        width = len(g[0]) if g else 0
        shifted = _shifted(g, 0, off)
        for r in range(height):
            out[r] += (
                shifted[r]
                if r < len(shifted)
                else [Cell("", None, (r, off + j)) for j in range(width)]
            )
        off += width
    return out


def compose_grid(layout) -> list[list[Cell]] | None:
    """布局 → 一张规则网格；没有表格的框架页（滚动条、空白页）直接略过。"""
    if isinstance(layout, str):
        return parse_grid(layout) if has_table(layout) else None
    kind, kids = layout
    grids = [g for k in kids if (g := compose_grid(k))]
    if not grids:
        return None
    return hstack_grids(grids) if kind == "cols" else stack_grids(grids)


def _region_names() -> set[str]:
    names: set[str] = set(_NATION)
    for r in regions.regions().values():
        if r.level in ("province", "special", "prefecture", "county"):
            for n in (r.name_zh, *r.aliases):
                names.add(n)
                for suffix in (
                    "省",
                    "市",
                    "自治区",
                    "特别行政区",
                    "壮族自治区",
                    "回族自治区",
                    "维吾尔自治区",
                ):
                    if n.endswith(suffix) and len(n) > len(suffix) + 1:
                        names.add(n[: -len(suffix)])
    return names


def _distinct(row: list[Cell]) -> list[Cell]:
    seen, out = set(), []
    for cell in row:
        if cell.origin not in seen:
            seen.add(cell.origin)
            out.append(cell)
    return out


def _is_year_row(row: list[Cell]) -> bool:
    nums = [parse_number(c.text) for c in _distinct(row) if c.text]
    nums = [n for n in nums if n is not None]
    return bool(nums) and all(float(n).is_integer() and 1900 <= n <= 2100 for n in nums)


def _numeric_cells(row: list[Cell], start: int) -> int:
    return sum(1 for c in row[start:] if parse_number(c.text) is not None)


def to_table(
    html: str | list[str] | tuple[str, list],
    *,
    default_region: str = "全国",
    default_year: str | None = None,
    source_url: str | None = None,
    title_hint: str | None = None,
) -> Table:
    """网页表格 → 整洁长表。default_region：行标签不是地区时用它；default_year：标题和表头里都没有年份时用它
    （普查资料的参考年；年鉴的出版年不是观测年，调用方不要传）。"""
    # html 可以是一个页面、一串页面（按顺序上下拼），或框架页的嵌套布局（rows 上下拼、cols 左右拼）
    layout = ("rows", list(html)) if isinstance(html, list) else html
    grid = parse_grid(layout) if isinstance(layout, str) else compose_grid(layout)
    if not grid:
        raise AdapterError("页面里没有找到表格")
    width = len(grid[0])
    warnings: list[str] = []

    # ── 标题与单位 ──
    title, unit_default, first_body = "", "", 0
    for i, row in enumerate(grid[:8]):
        d = [c for c in _distinct(row) if c.text]
        # 标题：前几行里独占一个单元格的、像一句话的文字（Excel 发布页通常合并整行，但也有不合并的）
        if not title and len(d) == 1 and len(d[0].text) >= 4 and not _UNIT_LINE.match(d[0].text):
            title = _TITLE_NO.sub("", d[0].text).strip()
            first_body = i + 1
            continue
        unit_line = next((m for c in d if (m := _UNIT_LINE.match(c.text))), None)
        if unit_line and len(d) <= 2:
            unit_default = unit_default or unit_line.group(1).strip()
            first_body = i + 1
    if not title and title_hint:  # 表里没有标题行（如框架页里只有数据框）：用目录里登记的标题
        title = _TITLE_NO.sub("", clean(title_hint)).strip()
    if not title:
        warnings.append("没有识别出标题行")

    # ── 数据区：第一个含数值（且不是整行年份）的行起，到最后一个含数值的行止；之后是脚注 ──
    body = grid[first_body:]
    numeric_idx = [
        i for i, row in enumerate(body) if _numeric_cells(row, 0) >= 1 and not _is_year_row(row)
    ]
    if not numeric_idx:
        raise AdapterError("没有在页面表格里找到数据行（可能是纯文字表或图片）")
    d0, d1 = numeric_idx[0], numeric_idx[-1]
    header_rows = [r for r in body[:d0] if any(c.text for c in r)]
    region_rows = [r for r in body[d0 : d1 + 1] if any(c.text for c in r)]
    active = max((max(j for j, c in enumerate(r) if c.text) + 1 for r in region_rows), default=1)

    def is_section(r: list[Cell]) -> bool:
        """没有数值、只有一个横跨半幅以上的合并单元格的行（段标题）。"""
        distinct = [c for c in _distinct(r) if c.text]
        spans = sum(1 for c in r[:active] if distinct and c.origin == distinct[0].origin)
        return _numeric_cells(r, 0) == 0 and len(distinct) == 1 and spans >= max(2, 0.5 * active)

    section = ""
    if len(header_rows) >= 2 and not header_rows[-1][0].text and is_section(header_rows[-1]):
        section = next(c.text for c in header_rows.pop() if c.text)  # 表头紧跟着的段标题（第一组）
    # 数据区里的"段标题行"（如一张表里叠着几组"按1970年价格/按1980年价格"）：之后的行都带上它
    data_rows: list[list[Cell]] = []
    sections: list[str] = []
    for r in region_rows:
        if is_section(r):
            section = next(c.text for c in r if c.text)
            continue
        data_rows.append(r)
        sections.append(section)
    notes = [
        " ".join(c.text for c in _distinct(r) if c.text)
        for r in body[d1 + 1 :]
        if any(c.text for c in r)
    ]

    # ── 行标签列：左侧连续的"非数值"列；第一列若是整数且基本递增（年份、年龄、序号）也是标签 ──
    def numeric_share(col: int) -> float:
        cells = [r[col].text for r in data_rows if r[col].text]
        return sum(parse_number(t) is not None for t in cells) / len(cells) if cells else 0.0

    def is_sequence(col: int) -> bool:
        vals = [
            v for r in data_rows if r[col].text and (v := parse_number(r[col].text)) is not None
        ]
        if len(vals) < 3 or any(not float(v).is_integer() for v in vals):
            return False
        return sum(1 for x, y in zip(vals, vals[1:], strict=False) if y > x) >= 0.9 * (
            len(vals) - 1
        )

    def year_share(col: int) -> float:
        cells = [r[col].text for r in data_rows if r[col].text]
        return sum(bool(_BARE_YEAR.match(t)) for t in cells) / len(cells) if cells else 0.0

    k = 0
    while (
        k < min(3, width)
        and any(r[k].text for r in data_rows)
        and (numeric_share(k) < 0.3 or (k == 0 and (is_sequence(0) or year_share(0) >= 0.6)))
    ):
        k += 1
    if k == 0:
        warnings.append("没有识别出行标签列，行标签用行号代替")
    if not header_rows:
        warnings.append("没有识别出表头，列用序号代替")

    # ── 列路径、列单位、列期别 ──
    def col_info(c: int) -> tuple[str, str, str]:
        items: list[str] = []
        for r in header_rows:
            t = r[c].text
            if not t or (items and items[-1] == t):
                continue
            if items and items[-1].endswith(
                ("、", "，", ",")
            ):  # 一个长表头被折成两行（"农、林、"+"牧、渔业"）
                items[-1] += t
            else:
                items.append(t)
        unit, path, year = "", [], ""
        year_is_period = not _YEAR_AS_CATEGORY.search(title)
        for i, t in enumerate(items):
            if m := _UNIT_LINE.match(t) or _PAREN_ONLY.match(t):
                unit = unit or m.group(1).strip()
                continue
            nxt = items[i + 1] if i + 1 < len(items) else ""
            if (
                year_is_period
                and not year
                and (ym := _BARE_YEAR.match(t))  # 只有"纯年份"才是观测期；"1949年及以前"之类是分类
                and not nxt.startswith(_CONTINUES)  # "1949年"+"及以前"：一个分类名被折成了两行
            ):
                year, t = ym.group(1), ""
            if not unit and (um := _TAIL_UNIT.match(t)) and um.group(1):  # "工业总产值（亿元）"
                t, unit = um.group(1).strip(), um.group(2).strip()
            if t and t not in path:
                path.append(t)
        return " · ".join(path), unit, year

    cols = {c: col_info(c) for c in range(k, width)}

    # ── 行标签：层级（缩进/"其中"/#）→ 完整标签；是年份、地区，还是并入指标 ──
    names = _region_names()
    stack: list[tuple[int, str]] = []
    h0 = next((r[0].text for r in header_rows if r[0].text), "") if k else ""

    def is_year_label(text: str) -> bool:
        return bool(re.fullmatch(r"(?:19|20)\d{2}\s*年?", text))

    def label_of(row: list[Cell]) -> str:
        parts = [row[j].text for j in range(k) if row[j].text]
        text = " · ".join(dict.fromkeys(parts))
        if is_year_label(text):
            return text  # 年份是期别，不参与层级
        depth = row[0].indent if k else 0
        child = text[:1] in ("#", "＃") or text.startswith("其中")
        text = re.sub(r"^(?:[#＃]\s*|其中\s*[:：]?\s*)", "", text)
        if parse_number(text) is not None and h0:  # "0""1""2"…是年龄/序号：带上表头名才有意义
            text = f"{h0} {text}"
        if child:  # "其中""#"说的是紧挨着的上一行的下级
            depth = (stack[-1][0] + 1) if stack else 1
        while stack and stack[-1][0] >= depth:
            stack.pop()
        full = " · ".join([*(t for _, t in stack), text]) if text else ""
        if text:
            stack.append((depth, text))
        return full

    labels = [label_of(r) for r in data_rows]
    flat = [x.split(" · ")[-1] for x in labels]
    year_flags = [is_year_label(x) for x in labels]
    non_total = [
        x for x, y in zip(flat, year_flags, strict=True) if x and not y and x not in _TOTALS
    ]
    region_mode = len(non_total) >= 3 and sum(
        1 for x in non_total if clean(x).replace(" ", "") in names
    ) >= 0.5 * len(non_total)
    year_mode = sum(year_flags) >= max(2, 0.6 * len(data_rows))
    # 标题里的年份只有"单一年份"才当期别；"1978～2012""2009.11.1-2010.10.31"这类区间说的是跨度，不是一个期别
    years_in_title = set(_YEAR.findall(title))
    single = len(years_in_title) == 1 and not _RANGE.search(title)
    title_year = (next(iter(years_in_title)) if single else "") or (default_year or "")

    out: list[list] = []
    numeric_total = skipped_text = 0
    for row, label, leaf, is_year, section in zip(
        data_rows, labels, flat, year_flags, sections, strict=True
    ):
        row_year = ym.group(1) if is_year and (ym := _YEAR.search(label)) else ""
        if is_year or not region_mode:
            region = default_region
        else:
            region = default_region if leaf in _TOTALS else ("全国" if leaf in _NATION else leaf)
        for c in range(k, width):
            cell = row[c]
            if not cell.text:
                continue
            value = parse_number(cell.text)
            if value is None and (_NA.fullmatch(cell.text) or cell.text in ("x", "X")):
                continue  # 缺省记号：不是数值也不是错误
            if value is None:
                skipped_text += 1
                continue
            numeric_total += 1
            path, unit, col_year = cols[c]
            year = col_year or row_year or title_year
            parts = [title] if title else []
            if section:
                parts.append(section)
            in_indicator = bool(label) and not is_year and not region_mode
            if in_indicator:
                parts.append(label)
            if path:
                parts.append(path)
            elif not in_indicator:
                parts.append(f"第{c + 1}列")
            out.append(
                [
                    f"{year}年" if year else "",
                    f"{year}YY" if year else "",
                    region,
                    " · ".join(p for p in parts if p),
                    value,
                    unit or unit_default,
                ]
            )
    if skipped_text:
        warnings.append(f"{skipped_text} 个单元格是文字（非数值），已跳过")
    confidence = "高"
    if any("没有识别出" in w for w in warnings):
        confidence = "低"
    elif k > 1:
        confidence = "中"
    return Table(
        title or "网页表格",
        out,
        meta={
            "来源": "统计年鉴/普查资料网页表格（解析）",
            "网址": source_url,
            "说明": "；".join(notes[:3]),
            "解析": {
                "数值单元格": numeric_total,
                "输出行": len(out),
                "表头行数": len(header_rows),
                "标签列数": k,
                "行标签含义": "期别" if year_mode else ("地区" if region_mode else "并入指标"),
                "可信度": confidence,
                "提示": warnings,
            },
        },
    )
