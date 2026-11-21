# DataFinder

**多领域、多来源的找数入口，能下载的直接给**：用关键词或一句话，跨领域找到**数据门户、数据集、数据表、指标**，告诉你**数据在哪、怎么拿**——能直接下载的给文件，需要通过接口取数的生成表格，只有链接的给准确链接和获取方式。中文优先。

> 状态：早期开发中（v0.0.x）。当前已接入的领域：**中国官方统计**（国家统计局发布库、统计年鉴、普查资料、统计公报与数据发布、省级统计局与部委的统计栏目）。框架按"核心通用、领域插件化"搭建，后续领域复用同一套机制。
> 三种用法：**命令行**、**本地网页**、**MCP（给 AI 助手用）**。

## 它怎么组织数据

目录里每个条目是一个**资源**（指标、数据表、数据集、统计库、年鉴系列/版次、门户……），用三个相互独立的**分面**描述，再加几个辅助筛选：

| 分面 | 回答 | 例子 |
|---|---|---|
| **主题** | 关于什么 | 工业与制造业 › 工业增加值；人口与人口结构；能源与碳排放…（可多选、有层级） |
| **地域** | 涉及哪里 | 全国 · 河北 · 港澳台 · 国际；并区分"数据覆盖哪里"与"按哪一级报告（省/地市/县）" |
| **资源类型** | 是什么形态 | indicator 指标 · table 数据表 · dataset 数据集 · database · series/edition 年鉴系列/版次 · portal 门户 |
| 辅助 | 观测期、频率、**获取方式**、核验状态 | 见下 |

**获取方式**（每条结果都标，三类）：

| 类 | 含义 | `datafinder get` 做什么 |
|---|---|---|
| ⬇ **download 可直接下载** | 目录里登记过的文件直链 | 下载到本机，附清单（来源、时间、体积、sha256、许可） |
| ⚙ **generate 需要生成** | 没有现成文件，要通过接口实时取数 | 取数 → 整洁长表 → 导出 CSV/Excel，附描述统计与覆盖检查 |
| ↗ **link 只有链接** | 图片、需登录/申请、站点拒绝机器人 | 给出链接和获取方式，不下载 |

几条贯穿始终的原则：**未知不等于满足**（宽松模式返回并标注缺什么，严格模式不算满足）；**出版年不是观测期**；**发布机构所在地不是数据覆盖地**；**标题推断的粒度不冒充已验证**；**图片年鉴能下载但不能直接分析**（usability 单独标注）；**下载只在你明确要求时发生**。

## 快速开始

需要 [uv](https://docs.astral.sh/uv/)。无需 Docker、数据库或 Node：

```bash
uv tool install git+https://github.com/EXIST-D/DataFinder

datafinder sync                              # 一次：门户库（约 7MB）
datafinder index pull                        # 一次：下载已发布的资源目录快照（约 45MB，几秒装好）
datafinder search "工业增加值" -r 河北         # 跨主题 × 地域 × 类型检索
datafinder get '<resource_id>' -r 河北 --yes   # 取数并导出（不加 --yes 只预览）
datafinder serve                             # 本地网页
```

从源码运行把 `datafinder` 换成 `uv run datafinder`。命令别名 `dfd`（不使用 `df`，它会遮蔽系统自带的磁盘命令）。

> 快照里只有名称、口径、链接等元数据（不含数值），装好后检索都在本地、毫秒级。想自己抓取或补全某个来源（快照里个别站点因网络原因缺失）用 `datafinder index build [来源]`——串行限速、可中断续跑，国家统计局发布库约 1 小时；本机有旧版本地索引则 `datafinder index migrate` 一次性迁入。本地目录比快照新时 `pull` 会拒绝覆盖（`--force` 才替换，旧库备份为 `catalog.db.bak`）。

## 命令行

```bash
# 找
datafinder search "工业增加值" -r 河北 -k indicator           # 河北能取数的指标
datafinder search "规模以上工业增加值" -r 山东 -k table -d download   # 山东年鉴里可下载的表（含各版次）
datafinder search -t cnstat.population -r 辽宁 -k table        # 不写关键词，只用筛选浏览
datafinder search "人口" --from 2015 --to 2020 --strict        # 严格模式：观测期未知的不算满足
datafinder facets                                              # 合法的主题树、地域、类型与获取方式（带计数）
datafinder inspect '<resource_id>' -c                          # 层级、分发入口、怎么获取、子资源（逐级浏览）
datafinder plan "工业质量体系评价" -r 河北省                     # 研究选题的找数方案（指标、数据表、官方入口、统计建议）

# 拿（默认只预览；--yes 才执行）
datafinder get '<resource_id>' -r 河北 -r 北京 --start 2015 -o 工业增加值.xlsx --yes
datafinder get '<resource_id>' --yes                           # 可直接下载的文件
datafinder describe 工业增加值.csv                              # 描述统计

# 维护
datafinder index pull [--from 目录或https地址]                   # 安装发布的目录快照
datafinder index build [适配器] --opt db=3,6                    # 抓取目录（国家统计局按库；年鉴/普查按系列：--opt series=cn,CN-SD,pop；官网栏目按机构：--opt site=nbs,CN-BJ）
datafinder index snapshot --out snapshot                       # （维护者）把当前目录打包成可发布的快照
datafinder index migrate / status
datafinder datasets "air quality"                              # 实时查外部数据平台（Zenodo、DataCite、各国开放数据…）
datafinder sources / doctor
```

取数输出是**整洁长表**（期别、地区、指标、数值、单位，每行一个观测值），CSV 带 BOM，Excel 直接打开不乱码；xlsx 含"说明"工作表。不指定路径时保存在 `~/.datafinder/exports/`，每个文件旁有 `*.manifest.json`（来源、请求参数、时间、sha256、许可）。取数后还会给**覆盖检查**：期望的 地区×指标×期别 单元里实际取到多少、缺哪些。时间格式：年度 `2015`、月度 `201501`、季度 `202401`（2024 年第 1 季）。

## 给 AI 助手用（MCP）

一句话需求，如"我要写一篇**河北省工业质量体系评价**的论文，帮我找数据来源"，助手可以：定位权威来源 → 预览并取数 → 导出 CSV/Excel → 给出描述统计、覆盖缺口与分析建议。

```bash
datafinder mcp --print-config        # 打印可粘贴到 Cursor / Claude Desktop / Claude Code 的配置
```

九个工具，按能力分四层，**工具名里没有领域词**（领域用参数区分）：

| 层 | 工具 | 作用 |
|---|---|---|
| L0 上下文 | `facets` · `sources` · `status` | 合法的主题/地域/类型；有哪些来源与能力；目录与门户库状态（`hints` 写明怎么办） |
| L1 发现 | `search` · `plan` | 多分面检索（目录检索不联网）；研究选题的找数方案 |
| L2 检视 | `inspect` | 一个资源的层级、分发入口、怎么获取、子资源 |
| L3 获取 | `fetch` · `download` | **默认只预览**；`confirm=true` 才联网取数/下载并写文件 |
| L4 后处理 | `describe` | 对已导出文件做描述统计 |

另有提示模板 `research_data_plan`。设计取向：**工具都是确定性的、不调用大模型**；理解题目、拆指标体系、写方案由助手自己做。错误统一为 `[错误码] 原因（下一步：…）`，如 `[INDEX_NOT_READY]`、`[SOURCE_RATE_LIMITED]`、`[CONFIRM_REQUIRED]`。MCP 里 `fetch` 只能写进导出目录，`download` 只接受目录里登记过的链接（不接受任意 URL，拒绝内网地址，限制重定向与体积）。

## 本地网页

`datafinder serve` 启动一个只监听本机的网页，顶部五个入口：

| 入口 | 做什么 |
|---|---|
| **搜索** | 实时查外部数据平台，结果逐批出现；详情给出地区、发布方、许可、获取方式与准确跳转 |
| **资源目录** | 主题磁贴 + 地域/类型/获取方式筛选 + 严格模式开关；每条标明可下载/需生成/仅链接；点开看层级与"怎么获取"（给出可复制的 `datafinder get` 命令——网页本身不执行下载） |
| **门户目录** | 22 个领域磁贴 + 国家→省、机构类型、数据形态、开放程度、API 筛选的门户列表 |
| **中国统计** | 国家统计局数据库逐级浏览与取数、31 个省级统计局入口（标注核验状态）、省市开放平台获取方式 |
| **检索入口** | Dateno、Google Dataset Search、Zenodo、Kaggle 等带着关键词一键跳转 |

视觉参考 Apple 官网的设计语言；跟随系统亮/暗色；完全离线可用（不加载外部字体/脚本/图片）；严格 CSP；`Host` 头必须是本机地址。端口被占用时：`datafinder serve --port 8788`。

## 目前接入的领域：中国官方统计

- **国家统计局发布库**：13 个数据库（月度/季度/年度、分省、主要城市、港澳台、国际）、约 8 万个指标，可实时取数（多地区、多指标，`["全部"]`=31 省）。**实验性**：新版发布库 2026-03 上线，接口无正式文档，且前面有访问防护——只在你主动抓取/取数时访问，串行限速，遇防护即停，**不重试、不绕过**。
- **统计年鉴**：中国统计年鉴 2005–2025（21 版）与山东、辽宁、河南、**上海**（2004–2025，树形目录）省级年鉴的数据表目录，同一张表的各版次归并；广东、湖北只有各版次列表，登记整本 zip/rar 下载与在线浏览入口。同一版次有 Excel 目录时**把 xls 并进来**，下载时优先给能直接分析的格式（xls > 网页表格 > 图片）。北京、河北、四川、黑龙江的地址模板已登记，但部分网络环境会超时或被拒绝，如实标注。
- **普查资料**：第七、六次全国人口普查与第一至五次全国经济普查的数据表（`census_product`，同一套目录模板，多数有 xls）。
- **统计公报与数据发布**：国家统计局的全国年度统计公报、各类普查公报、**数据发布与数据解读**（月度/季度发布，约 2000 条），以及各省统计局、部委网站的"统计公报/统计数据/数据发布/普查"栏目（`bulletin` / `report`）——从机构首页导航自动发现栏目，能解析成"带日期的条目列表"的逐页收录，解析不了的栏目只作入口，**不硬凑条目**。条目大多是网页（只有链接），带 PDF/Excel 附件的可下载。
- **官方入口**：31 个省级统计局、27 个国家部委等发布机构的网站入口与栏目（标注核验状态；站点拒绝或连不上时只保留首页并写明原因，换个网络后 `index build site` 补全）。

> 覆盖现状：官网栏目的条目收录取决于站点是否可访问、栏目页能否被通用解析。2026-11-21 在一个国内网络环境实测，约 1/3 的省级统计局和 4 个部委的栏目被收录，其余因超时/拒绝（HTTP 403/412）、动态页（列表由脚本加载）而只有入口。这些站点换个网络通常可以补全。

**国家统计局有 MCP 吗？**（2026-11-20 调研）没有官方 MCP。社区项目有 [`icen-ai/mcp-cnbs`](https://github.com/icen-ai/mcp-cnbs)（Apache-2.0，还聚合世界银行、IMF、OECD 等）、`Eliseowzy/cnbs-mcp-server`、`Ddhjx-code/national_data`、`pipeworx-io/mcp-china-macro`；它们都封装同一个非公开网页接口。DataFinder 直接调用接口，不依赖它们，你也可以把它们和 DataFinder 一起挂进助手。

## 其他来源（`datafinder datasets` 与 `search --live`）

| 来源 | 说明 |
|---|---|
| Zenodo、DataCite | 科研数据；DataCite 覆盖上千个仓库，含中国科学数据银行 ScienceDB。中文问句会**中英文各查一次** |
| HDX、data.gov.uk、加拿大、澳大利亚 | 政府/人道数据（CKAN） |
| Dataseek（可选） | 约 60 个长尾国际来源 |

单个来源失败、超时或限流时，其余照常返回，并标明每个来源的状态；**失败不会被当成"0 条结果"**。

## 它怎么理解中文

- **自研分词**：拉丁词 + 中文二元组，"人口""气象"这类两字词也能命中。
- **检索单元 + 术语词表**：一句话拆成概念单元，每个单元带"概念 → 统计里实际使用的名称"的同义词（如 `研发 → 研究与试验发展`），整词命中、分类路径命中、部分重合分别计分，并保证每个概念都有代表、同模板指标不刷屏。词表在领域包里，**加一行即生效**。
- **中英词表**：`人口普查 → population census`，国际来源里的英文记录也能被中文问句召回。
- **识别意图**：地区（`辽宁`、`美国`）、格式、时间（`近十年`）、行业。

## 效果评估

三套基准都在本机运行、可复现，**开发集用来调参，留出集只用来估计没调过的新题**：

```bash
datafinder bench topics [--holdout|--public]   # 真实选题验收：找数方案是否给出该有的门户/指标/数据表/公报（--public = 公开来源选题集）
datafinder bench facets [--holdout]    # 分面检索验收：筛选语义，尤其是"必须不出现"的误导项
datafinder bench run                   # 排序基准：14 条中文问句 + 约 580 条标注，比较排序方案
```

局限（务必知道）：样本小、标注者是 AI；开发集分数偏乐观，**留出集才是对"新问题"的估计**。公开来源选题集（取自 CT2C-QA、统计建模大赛获奖选题、NTCIR Data Search 题型）第一次跑的成绩是 **59/76（78%）**，其中真正的公开题 73%——这比我们自己的开发/留出集低得多，说明自拟题集偏乐观；缺口主要是术语词表（如"PM2.5/空气质量"到"生态环境状况公报"）和部委/省局网站的覆盖。方法与结果见 [bench/README.md](bench/README.md)。

## 扩展

| 想做什么 | 怎么做 |
|---|---|
| 新增 CKAN 类门户 | `src/datafinder/data/sources.yaml` 加一行 |
| 新增/修正门户 | `src/datafinder/data/curated/portals.yaml` 加一条（新增须自己访问核验） |
| 新增一个领域或来源 | 建 `domains/<id>/`（适配器 + 分类词典 + 术语词表 + `plugin.py`），先写选题与分面验收集；详见 [CONTRIBUTING.md](CONTRIBUTING.md) |
| 新增中文词 / 行业 / 统计术语 | `lexicon.zh-en.yaml` / `industries.yaml` / 领域包 `thesaurus.zh.yaml` |

## 数据来源与署名

见 [NOTICE](NOTICE)。门户目录来自 [Dateno dataportals-registry](https://github.com/datenoio/dataportals-registry)（CC BY 4.0），本仓库不包含该数据，由 `sync` 在你本机下载。界面所列门户**未实时核验**；数据集的许可以原站为准，"许可未知"不代表可以随意使用。国家统计局等官方数据的数值版权归原发布机构，请以官网为准。

## 开发

```bash
uv sync --extra dev
uv run pytest                                    # 单元测试（全部离线）
uv run ruff check . && uv run ruff format --check .
```

目录结构（核心通用、领域插件化；分层由 `tests/architecture` 守护）：

```
src/datafinder/
  catalog/        统一资源目录：资源类型 · 地域 · 主题 · 分类规则 · 存储与检索 · 适配器协议 · 受控下载 · 栏目页解析与官网栏目适配器（listing/sites）· 目录快照（snapshot）
  service/        共用服务层（CLI / MCP / 网页都走这里）：discover · detail · acquire · plan · index · export · overview
  cli/  mcp_server/  server/    三个入口
  domains/        领域包（连接器、分类词典、术语词表、数据文件、plugin.py）
    official_statistics_cn/     中国官方统计：nbs（国家统计局）· yearbook（统计年鉴与普查资料）· sites（官网栏目配置）· legacy（旧索引迁移）
  portals/        门户目录：同步、分类、浏览、检索
  sources/        通用实时来源（Zenodo、DataCite、CKAN、Dataseek）
  engine/ query/  并发检索与 HTTP/缓存/去重/排序；分词、词表、检索计划、检索单元
  bench/          排序基准 · 选题验收 · 分面检索验收
  data/           核心数据文件：词表、行业、地区、资源类型、精选门户、来源登记
tests/            与 src 一一对应；architecture/ 守护分层；fixtures/ 放缩小的真实响应样本
bench/            基准数据：问句与标注、选题与分面验收集（开发集 + 留出集）
```

## 许可

[MIT](LICENSE)
