"""MCP 服务（stdio）：让 Cursor / Claude 等 AI 助手直接调用 DataFinder 的找数与取数能力。

九个工具，按能力分四层，工具名里没有任何领域词（领域用参数区分）：
  L0 上下文  facets · sources · status      只读、本地、毫秒级
  L1 发现    search · plan                  只读；目录检索不联网；永不下载
  L2 检视    inspect                        只读；层级、分发入口、怎么获取
  L3 获取    fetch · download               默认只预览；confirm=true 才联网取数/下载并写文件
  L4 后处理  describe                       对已导出文件做描述统计
工具都是确定性的、不调用任何大模型：理解题意、拆指标、写方案是助手自己的事。
"""

from __future__ import annotations

import functools
import inspect as pyinspect
from collections.abc import Callable
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from .. import service
from ..service import ServiceError, translate

INSTRUCTIONS = """\
DataFinder：多领域、多来源的找数与取数工具（当前已接入：中国官方统计）。

典型流程（用户说"我要写 XX 论文/做 XX 分析，需要哪些数据"）：
1. plan(topic, region)：拆出可取数的指标、数据表、目标地区与领域的官方入口，以及统计分析建议。
2. 需要更细：facets 看合法的主题/地域/类型 → search 按分面筛选 → inspect 看一个资源的层级与怎么获取。
3. 拿数据：看 delivery ——
   generate（需要生成）→ fetch；download（可直接下载）→ download；link（只有链接）→ 把链接和获取方式告诉用户。
   fetch/download **默认只预览**（返回将做什么、缺什么参数），用户同意后再带 confirm=true 执行。
4. 汇报时：给出每条数据的来源链接、取数时间、统计口径；标明哪些数据无法自动获取（需登录/申请/站点拒绝机器人）。

约定：
- 不要编造数据或链接：数值只来自 fetch/download 的返回。
- usability=image 的文件（如图片年鉴）能下载但不能直接分析。
- 返回里的 verification_level / missing_requirements 告诉你"信息是确认的、推断的还是缺失的"，不要把未知当成满足。
- 来源有访问防护：遇到 SOURCE_RATE_LIMITED 要停下来告诉用户稍后再试，不要反复重试。
- 错误以 [错误码] 开头，后面是原因和下一步。
"""

PROMPT = """\
我想做一项研究：「{topic}」{region_part}。请帮我定位数据来源并给出初步的数据与统计方案。

请按下面步骤做：
1. 调用 plan 了解可用的来源；必要时用 facets / search / inspect 补充。
2. 把研究主题拆成 3–5 个维度，每个维度列出候选指标，并标注：指标名、来源（含链接）、口径/单位、覆盖期、获取方式（delivery）、是否能自动取数。
3. 对 delivery=generate 的核心指标，先 fetch 预览，征得我同意后 confirm=true 取回最近若干年的数据，并用返回的描述统计与覆盖检查写出趋势、地区差异与数据缺口的要点。
4. 输出：① 数据来源清单表；② 指标体系草案；③ 描述性统计方案（统计量、图表、比较对象）；④ 数据缺口与获取建议（需要登录/申请/付费的来源）。
不要编造数值或链接；无法获取的数据明确说明。"""


def _format(err: ServiceError) -> str:
    tail = f"（下一步：{err.next_action}）" if err.next_action else ""
    return f"[{err.code}] {err}{tail}"


def guard(fn: Callable[..., Any]) -> Callable[..., Any]:
    """把各层异常翻译成带错误码的 ToolError：MCP SDK 只会把 ToolError 的原文交给助手，
    其他异常的消息会被隐去，助手就不知道该怎么纠正。"""
    if pyinspect.iscoroutinefunction(fn):

        @functools.wraps(fn)
        async def awrapper(*a: Any, **kw: Any) -> Any:
            try:
                return await fn(*a, **kw)
            except ToolError:
                raise
            except Exception as exc:  # noqa: BLE001 —— 协议边界：统一翻译
                raise ToolError(_format(translate(exc))) from exc

        return awrapper

    @functools.wraps(fn)
    def wrapper(*a: Any, **kw: Any) -> Any:
        try:
            return fn(*a, **kw)
        except ToolError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise ToolError(_format(translate(exc))) from exc

    return wrapper


def build_server() -> MCPServer:
    mcp = MCPServer("datafinder", instructions=INSTRUCTIONS)

    def tool(**kw: Any) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        def deco(fn: Callable[..., Any]) -> Callable[..., Any]:
            return mcp.tool(**kw)(guard(fn))

        return deco

    # ── L0 上下文 ──
    @tool()
    def facets(domain_id: str | None = None) -> dict[str, Any]:
        """【L0】合法的主题树、地域、资源类型与获取方式（带当前目录里的计数）。search 的筛选参数从这里取值。

        topics：主题树（level 1/2，aliases 可当检索词）；regions：目录里出现过的地域；kinds：资源类型；
        delivery：获取方式（download 可直接下载 / generate 需要生成 / link 只有链接）。
        """
        return service.facets(domain_id)

    @tool()
    def sources() -> dict[str, Any]:
        """【L0】目录里有哪些来源（含能力、资源数、可访问状态）、可实时检索的外部来源、门户库规模。"""
        return service.sources()

    @tool()
    def status() -> dict[str, Any]:
        """【L0】DataFinder 状态：目录与门户库规模、导出目录、是否有待迁移的旧索引。出问题时先看这个（hints 写明怎么办）。"""
        return service.status()

    # ── L1 发现 ──
    @tool()
    async def search(
        query: str = "",
        domain_ids: list[str] | None = None,
        topic_ids: list[str] | None = None,
        region_ids: list[str] | None = None,
        region_match: str = "intersects",
        geo_granularity: str | None = None,
        kinds: list[str] | None = None,
        delivery: list[str] | None = None,
        period_start: int | None = None,
        period_end: int | None = None,
        frequency: str | None = None,
        mode: str = "discovery",
        limit: int = 20,
        offset: int = 0,
        include_facets: bool = True,
        live: bool = False,
    ) -> dict[str, Any]:
        """【L1】跨主题 × 地域 × 资源类型检索（目录检索不联网，毫秒级）。同一分面多选 OR，不同分面 AND。

        query：自然语言或关键词；topic_ids：主题 id/中文名（含下级主题）；region_ids：CN-HE / 河北 / 130000 均可；
        region_match：intersects（含"覆盖该地区"的上级资源，如全国分省库）或 exact；geo_granularity：country/province/prefecture/county；
        kinds：indicator 指标 / table 数据表 / dataset 数据集 / database / series / edition / portal …；
        delivery：download / generate / link；period_*：观测期（年）；frequency：annual/quarterly/monthly。
        mode：discovery（默认，高召回：信息未知的也返回，并在 missing_requirements 里写缺什么）/ strict（高精度：未知、未验证不算满足）。
        返回：results（含 why_matched、delivery、usability、verification_level）、facets（当前候选集的分面计数）、portals（相关门户，永远只有链接）。
        live=true 才会实时请求外部数据平台（Zenodo、DataCite、各国开放数据等），结果放在 live 里，来源失败不等于没有数据。
        """
        return await service.search(
            query,
            domain_ids=domain_ids,
            topic_ids=topic_ids,
            region_ids=region_ids,
            region_match=region_match,
            geo_granularity=geo_granularity,
            kinds_=kinds,
            delivery=delivery,
            period_start=period_start,
            period_end=period_end,
            frequency=frequency,
            mode=mode,
            limit=limit,
            offset=offset,
            facets=include_facets,
            live=live,
        )

    @tool()
    def plan(topic: str, region: str | None = None) -> dict[str, Any]:
        """【L1·起点】为一个研究/分析主题给出找数方案：可取数的指标（带取数参数）、数据表、目标地区与领域的官方入口、继续检索的入口、统计分析建议。

        topic：自然语言主题，如"工业质量体系评价"；region：省/市/国家名，如"河北省"（也可直接写进 topic）。
        纯本地计算、秒回、不联网。
        """
        return service.plan_sources(topic, region)

    # ── L2 检视 ──
    @tool()
    def inspect(
        resource_id: str, children: bool = False, limit: int = 50, offset: int = 0
    ) -> dict[str, Any]:
        """【L2】看清一个资源：所在层级、分发入口与获取方式（how_to_get）、取数需要什么参数、子资源。

        resource_id 来自 search/plan 的结果。容器类资源（库、数据集、系列、版次、分类）会列出子资源（children），
        逐级向下就能浏览整棵目录树。
        """
        return service.inspect(resource_id, children=children, limit=limit, offset=offset)

    # ── L3 获取（默认只预览）──
    @tool()
    async def fetch(
        resource_id: str,
        regions: list[str] | None = None,
        indicators: list[str] | None = None,
        start: str | None = None,
        end: str | None = None,
        periods: int = 8,
        format: str = "csv",
        confirm: bool = False,
        describe: bool = True,
    ) -> dict[str, Any]:
        """【L3】对有"生成"入口的资源实时取数 → 整洁长表（期别、地区、指标、数值、单位），导出 CSV/xlsx，附描述统计与覆盖检查。
        生成入口有两类：接口（api，如国家统计局、香港、台湾）和**网页表格**（html_parse，统计年鉴/普查的网页版表格，实时解析）。
        表同时有 xls 时默认走下载（见 inspect 的 how_to_get），但你可以 fetch 它的网页版得到整洁长表。
        网页表格的结果带 meta.解析：数值单元格数 = 输出行数（没丢没造）、表头/标签识别情况、可信度；可信度为"低"时请提醒用户核对原表。

        **不带 confirm 只返回将做什么与参数**；用户同意后 confirm=true 才联网取数并写文件。
        regions：地区名/代码，如 ["河北","北京"]，["全部"]=全部省份（分地区资源必填）；indicators：数据集级取数时选哪些指标（名称或 id，最多 10 个）；
        时间：给 start/end（年度 "2015"；月度 "201501"；季度 "202401"）取区间，否则取最近 periods 期；format：csv（Excel 可直接打开）或 xlsx。
        返回 saved_to（文件路径）、前 40 行预览、coverage（期望 vs 实际取到的单元数与缺口）。
        """
        return await service.fetch(
            resource_id,
            regions=regions,
            indicators=indicators,
            start=start,
            end=end,
            periods=periods,
            fmt=format,
            confirm=confirm,
            describe=describe,
        )

    @tool()
    def download(
        resource_id: str, distribution_index: int | None = None, confirm: bool = False
    ) -> dict[str, Any]:
        """【L3】下载 delivery=download 的文件（目录里登记过的直链），保存到导出目录并附清单（来源、时间、体积、sha256、许可）。

        **不带 confirm 只返回将下载什么**（格式、usability、许可、其他可选文件）；确认后 confirm=true 才下载。
        usability=image 的文件能下载但不能直接做数据分析。只下载目录里登记的链接，不接受任意 URL。
        """
        return service.download(resource_id, distribution_index=distribution_index, confirm=confirm)

    # ── L4 后处理 ──
    @tool()
    def describe(file: str) -> dict[str, Any]:
        """【L4】对 fetch 导出的文件（csv/xlsx，位于导出目录）重新计算描述统计：各指标×地区的均值、中位数、标准差、变异系数、增速，以及最新一期的地区排名。"""
        from ..tables import describe as describe_table
        from ..tables import describe_markdown

        result = describe_table(service.load_table(file))
        return {**result, "markdown": describe_markdown(result)}

    @mcp.prompt(title="研究选题找数")
    def research_data_plan(topic: str, region: str = "") -> str:
        """给定研究主题（和可选地区），引导助手完成：定位数据来源 → 取数 → 指标体系与描述统计方案。"""
        return PROMPT.format(topic=topic, region_part=f"（地区：{region}）" if region else "")

    return mcp


def run() -> None:
    build_server().run("stdio")
