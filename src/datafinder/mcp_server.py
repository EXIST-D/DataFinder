"""MCP 服务（stdio）：让 Cursor / Claude 等 AI 助手直接调用 DataFinder 的找数与取数能力。

工具都是确定性的、不调用任何大模型：理解主题、拆指标、写分析方案是助手自己的事，
这里只提供它做不到的——权威来源目录、官方统计接口的实时取数、导出与描述统计。
"""

from __future__ import annotations

import functools
import inspect
from collections.abc import Callable
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from . import service
from .service import ServiceError

INSTRUCTIONS = """\
DataFinder：中文优先的找数与取数工具，数据以中国官方来源为主。

典型流程（用户说"我要写 XX 论文/做 XX 分析，需要哪些数据"）：
1. plan_data_sources(topic, region)：得到可直接取表的国家统计局指标、目标地区的官方入口、领域权威门户。
2. 对 direct 里的指标调用 nbs_fetch：实时取数，自动保存 CSV/Excel，并返回描述统计。
3. 指标不全时：nbs_find 按关键词查指标；nbs_browse 逐级浏览；datasets_search 搜科研与开放数据平台。
4. 向用户汇报时：给出每条数据的来源链接、取数时间与统计口径；标明哪些数据无法自动获取（需要登录/申请密钥/站点拒绝机器人）。

注意：国家统计局接口有访问防护，请勿并发或高频调用 nbs_*；遇到"访问防护"错误应停下来告诉用户稍后再试。
不要编造数据或链接：所有数值都必须来自 nbs_fetch 等工具的返回。
"""

PROMPT = """\
我想做一项研究：「{topic}」{region_part}。请帮我定位数据来源并给出初步的数据与统计方案。

请按下面步骤做：
1. 调用 plan_data_sources 了解可用的权威来源；必要时用 nbs_find / portals_list / datasets_search 补充。
2. 把研究主题拆成 3–5 个维度，每个维度列出候选指标，并标注：指标名、来源（含链接）、口径/单位、覆盖年份、是否能自动取数。
3. 对能自动取数的核心指标调用 nbs_fetch 取回最近若干年的数据，并用返回的描述统计写出趋势与地区差异的要点。
4. 输出：① 数据来源清单表；② 指标体系草案；③ 描述性统计方案（用哪些统计量、图表、比较对象）；④ 数据缺口与获取建议（需要登录/申请/付费的来源）。
不要编造数值或链接；无法获取的数据明确说明。"""


def guard(fn: Callable[..., Any]) -> Callable[..., Any]:
    """把我们写给用户看的 ServiceError 转成 ToolError：MCP SDK 只会把 ToolError 的原文交给助手，
    其他异常的消息会被隐去，助手就不知道该怎么纠正（如"请先运行 datafinder sync"）。"""
    if inspect.iscoroutinefunction(fn):

        @functools.wraps(fn)
        async def awrapper(*a: Any, **kw: Any) -> Any:
            try:
                return await fn(*a, **kw)
            except (ServiceError, ValueError) as exc:
                raise ToolError(str(exc)) from exc

        return awrapper

    @functools.wraps(fn)
    def wrapper(*a: Any, **kw: Any) -> Any:
        try:
            return fn(*a, **kw)
        except (ServiceError, ValueError) as exc:
            raise ToolError(str(exc)) from exc

    return wrapper


def build_server() -> MCPServer:
    mcp = MCPServer("datafinder", instructions=INSTRUCTIONS)

    def tool(**kw: Any) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        def deco(fn: Callable[..., Any]) -> Callable[..., Any]:
            return mcp.tool(**kw)(guard(fn))

        return deco

    @tool()
    def datafinder_status() -> dict[str, Any]:
        """查看 DataFinder 状态：门户库数量、国家统计局指标索引是否已建立、导出目录等。出问题时先看这个。"""
        return service.status()

    @tool()
    def plan_data_sources(topic: str, region: str | None = None) -> dict[str, Any]:
        """【起点】为一个研究/分析主题给出找数方案：可直接取表的指标、目标地区官方入口、领域权威门户、继续检索的入口，以及描述统计建议。

        topic：用自然语言写主题，如"工业质量体系评价"；region：省/市/国家名，如"河北省"（也可直接写进 topic）。
        纯本地计算、秒回，不联网。返回的 direct 条目带 fetch 参数，可原样传给 nbs_fetch。
        """
        return service.plan_sources(topic, region)

    @tool()
    async def datasets_search(query: str, limit: int = 10) -> dict[str, Any]:
        """跨来源检索具体数据集（国家统计局、Zenodo、DataCite、HDX、各国开放数据等），返回标题、发布方、许可、格式和下载/API 资源。

        中文问句会自动中英文各查一次。较慢（5–20 秒）。注意看返回的 sources 里每个来源的状态：失败不代表没有数据。
        """
        return await service.datasets_search(query, limit)

    @tool()
    def portals_list(
        domain: str | None = None,
        country: str | None = None,
        region: str | None = None,
        official_only: bool = False,
        label: str | None = None,
        api_only: bool = False,
        page: int = 1,
        size: int = 15,
    ) -> dict[str, Any]:
        """按条件浏览数据门户目录（4 万+）。

        domain：领域 id（statistics 统计、economy 宏观经济、finance 金融、population 人口社会、government 政务公共、education 教育、
        health 医疗卫生、agriculture 农业、energy 能源、environment 生态环境、climate 气象气候、transport 交通物流、geospatial 地理空间、
        science 科学研究、housing 住房建筑、industry 工业制造、trade_market 数据交易 等）；country：ISO 码如 CN、US；
        region：省级码如 CN-HE（河北）、CN-GD（广东）；label：stats_bureau 省级统计局 / ministry 国家部委 / open_platform 省市开放平台。
        返回的 verify=blocked 表示站点拒绝自动访问，需用浏览器打开；note 里写了获取方式（如需注册密钥）。
        """
        return service.portals_list(
            domain=domain, country=country, region=region, official=official_only,
            label=label, api=api_only, page=page, size=size,
        )

    @tool()
    def nbs_databases() -> list[dict[str, Any]]:
        """国家统计局数据发布库的 13 个数据库（月度/季度/年度、分省、主要城市、港澳台、国际）。regional=true 表示有地区维度。"""
        return service.nbs_databases()

    @tool()
    def nbs_find(
        query: str, db: str | None = None, regional: bool | None = None, limit: int = 15
    ) -> dict[str, Any]:
        """在本地指标索引里按关键词查国家统计局指标（含所在分类路径、单位、统计口径）。需先在终端运行 `datafinder nbs index`。

        query 里的地区名会被忽略（指标本身不带地区）。regional=true 只看分省/城市库，false 只看全国口径。
        命中项的 db、cid、indicator_id 直接传给 nbs_fetch。
        """
        return service.nbs_find(query, db=db, regional=regional, limit=limit)

    @tool()
    async def nbs_browse(db: str, node_id: str = "") -> dict[str, Any]:
        """逐级浏览国家统计局分类树。node_id 为空时给出该库第一层；is_leaf=true 的节点 id 即 cid，供 nbs_indicators / nbs_fetch 使用。

        每次展开一层都会访问官方站点（结果缓存 24 小时），请有目的地浏览，不要批量遍历。
        """
        return await service.nbs_browse(db, node_id)

    @tool()
    async def nbs_indicators(cid: str) -> dict[str, Any]:
        """列出一个叶子数据集（cid）下的全部指标（含统计口径、单位）和可选地区。"""
        return await service.nbs_indicators(cid)

    @tool()
    async def nbs_search_national(keyword: str, limit: int = 12) -> list[dict[str, Any]]:
        """官方全局搜索（只有全国口径，匹配较模糊，搜"国内生产总值"这类标准名称最准）。返回最新值与可取数的 cid/indicator_id。"""
        return await service.nbs_search_national(keyword, limit)

    @tool()
    async def nbs_fetch(
        db: str,
        cid: str,
        indicators: list[str],
        regions: list[str] | None = None,
        start: str | None = None,
        end: str | None = None,
        n: int = 8,
        format: str = "csv",
        describe: bool = True,
    ) -> dict[str, Any]:
        """实时从国家统计局取数，得到整洁长表（期别、地区、指标、数值、单位），自动保存文件并附描述统计。

        indicators：指标 id 或名称（最多 10 个）；regions：地区名/12 位代码，如 ["河北","北京"]，["全部"]=全部省份（分省库必填，全国口径库可省略）；
        时间：给 start/end 取区间（年度 "2015"；月度 "201501"；季度 "202401" 即 2024 年第 1 季），否则取最近 n 期；
        format：csv（Excel 可直接打开）或 xlsx（含"说明"工作表）。返回 saved_to 为文件路径，rows 只含前 40 行预览，完整数据在文件里。
        """
        table = await service.nbs_fetch(
            db, cid, indicators, regions, start=start, end=end, n=n
        )
        return service.table_result(table, fmt=format, stats=describe)

    @tool()
    def describe_table(file: str) -> dict[str, Any]:
        """对 nbs_fetch 导出的文件（csv/xlsx，位于导出目录）重新计算描述统计：各指标×地区的均值、中位数、标准差、变异系数、增速，以及最新一期的地区排名。"""
        from .tables import describe, describe_markdown

        result = describe(service.load_table(file))
        return {**result, "markdown": describe_markdown(result)}

    @mcp.prompt(title="研究选题找数")
    def research_data_plan(topic: str, region: str = "") -> str:
        """给定研究主题（和可选地区），引导助手完成：定位数据来源 → 取数 → 指标体系与描述统计方案。"""
        return PROMPT.format(topic=topic, region_part=f"（地区：{region}）" if region else "")

    return mcp


def run() -> None:
    build_server().run("stdio")
