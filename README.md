# DataFinder

中文优先的**找数入口**：用关键词或一句话，快速找到各行业的**数据门户**、**数据集**、**数据表**，以及它们的**下载 / API 地址**。

> 状态：早期开发中（v0.0.x）。**已可用：找数方案、门户与数据集检索、国家统计局取数与导出、描述统计，三种用法：命令行、本地网页、MCP（给 AI 助手用）**。

## 快速开始

需要 [uv](https://docs.astral.sh/uv/)。**一条命令安装**，无需 Docker、数据库或 Node：

```bash
uv tool install git+https://github.com/EXIST-D/DataFinder

datafinder sync                             # 首次：下载并建立本地门户库（约 7MB）
datafinder serve                            # 打开本地网页
datafinder search "中国各省近十年人口 GDP 城镇化率"
```

下面的示例用 `uv run datafinder …`（在源码目录里运行）；用 `uv tool install` 装好后直接写 `datafinder …` 即可。从源码运行：

```bash
git clone https://github.com/EXIST-D/DataFinder.git && cd DataFinder && uv sync
uv run datafinder sync                      # 首次：下载并建立本地门户库（约 7MB）

# 找数据集（一次查多个来源，合并去重）
uv run datafinder search "中国各省近十年人口 GDP 城镇化率 CSV"
uv run datafinder search "国内生产总值" -r  # -r 同时显示下载/API 资源
uv run datafinder search "air quality" -n 5

# 找数据门户（哪些网站可能有这类数据）
uv run datafinder portals "气象"
uv run datafinder portals "辽宁 开放数据"

uv run datafinder serve                     # 打开本地网页 http://127.0.0.1:8787
uv run datafinder sources                   # 有哪些来源、状态如何
uv run datafinder doctor                    # 体检
```

命令名为 `datafinder`（短别名 `dfd`）。不使用 `df`，因为它会遮蔽系统自带的磁盘命令。

`search` 常用选项：`-n` 条数、`-r` 显示资源、`-s/-x` 指定/排除来源、`--refresh` 忽略缓存、`--json` 机器可读、`--explain` 查看查询计划与排序依据。

## 本地网页

`datafinder serve` 启动一个只监听本机的网页，顶部四个入口：

| 入口 | 做什么 |
|---|---|
| **搜索** | 一个搜索框，结果**逐批出现**（哪个来源先返回就先显示），右侧是相关门户和"在别的引擎继续搜"。点开数据集看**地区、发布方、数据期、许可、获取方式、下载/API 资源**（含可复制的请求体），以及"打开原站 / DOI / 所属门户"等准确跳转 |
| **分类浏览** | 不用打字：22 个**领域**磁贴 + 左侧筛选（国家→省、机构类型 6 级与"仅官方"、数据形态、开放程度、是否有 API），每个选项旁边都有条数；状态写进网址，可收藏分享 |
| **中国统计** | 国家统计局发布库 13 个数据库逐级点开 → 指标 → 选地区取最近 N 期（可复制 CSV 和接口请求体）；31 个省级统计局入口（标注了哪些**已核验可访问**、哪些会拒绝自动访问）；北京/上海/浙江开放平台的获取方式 |
| **检索入口** | Dateno、Google Dataset Search、data.europa.eu、Zenodo、Kaggle、Hugging Face、国家数据、必应找表格文件…带着你的关键词一键跳转；国际引擎用我们翻译好的英文检索词 |

- 视觉参考 Apple 官网的设计语言：系统字体栈（SF Pro / 苹方）、一个蓝色强调色、胶囊搜索框、白色分组面板、毛玻璃只用于顶栏与浮层；跟随系统亮/暗色与"减少动态效果"；手机上筛选可折叠。
- 完全离线可用：不加载任何外部字体、脚本或图片；严格 CSP；`Host` 头必须是本机地址；`inspect` 只接受本次搜索里出现过的链接。
- 国家统计局接口有访问防护：只在你点开时才逐级加载并缓存 24 小时，同一站点串行访问；一旦被要求验证就暂停，**不会重试或绕过**，页面会提示稍后再试。
- 端口被占用时：`datafinder serve --port 8788`。

## 给 AI 助手用（MCP）

一句话需求，如"我要写一篇**河北省工业质量体系评价**的论文，帮我找数据来源"，助手可以直接：定位权威来源 → 实时取国家统计局的表 → 导出 CSV/Excel → 给出描述统计与分析建议。

```bash
datafinder mcp --print-config        # 打印可粘贴到 Cursor / Claude Desktop / Claude Code 的配置
datafinder sync                      # 一次：门户库
datafinder nbs index                 # 一次：国家统计局指标索引（可中断续跑；没有它也能用，只是找指标要靠逐级浏览）
datafinder yearbook index            # 一次：统计年鉴目录（几分钟）
```

| 工具 | 作用 |
|---|---|
| `plan_data_sources` | **起点**：给主题（和地区），返回可直接取表的指标、目标地区官方入口、领域权威门户、检索入口与统计建议。纯本地，秒回 |
| `nbs_fetch` | 实时取数 → 整洁长表，自动保存 CSV/xlsx，附描述统计。可一次取多指标、多地区（`["全部"]`=31 省） |
| `nbs_find` / `nbs_browse` / `nbs_indicators` / `nbs_search_national` | 找指标：本地索引关键词查 / 逐级浏览分类树 / 看数据集下的指标与地区 / 官方全局搜索（全国口径） |
| `yearbook_find` / `yearbook_catalog` | 统计年鉴目录：按关键词找数据表，列出每张表有哪些年份版次、链接与格式；各年鉴系列收录情况 |
| `portals_list` | 按领域、国家、省、"仅官方"、专区（省级统计局 / 国家部委 / 省市开放平台）浏览门户目录 |
| `datasets_search` | 跨来源检索具体数据集（科研、开放数据、各国政府） |
| `describe_table` | 对导出文件重算描述统计（均值、中位数、变异系数、年均增速、最新一期地区排名） |
| `datafinder_status` | 门户库、指标索引、导出目录状态 |

另有提示模板 `research_data_plan`（研究选题找数）。设计取向：**工具都是确定性的、不调用大模型**；理解题目、拆指标体系、写方案由助手自己做，DataFinder 提供它做不到的——权威来源目录、官方接口实时取数、导出与统计。所有工具的错误信息都写明下一步怎么办。

## 命令行取数

```bash
datafinder plan "工业质量体系评价" -r 河北省          # 找数方案（--json 给脚本用）
datafinder nbs find "规模以上工业企业 利润" --regional  # 在指标索引里找
datafinder nbs get --db 6 --cid <cid> -i 工业增加值 -r 河北 -r 北京 --start 2015 -o 工业增加值.xlsx
datafinder nbs get --db 6 --cid <cid> -i <指标id> -r 全部 -n 5     # 31 省最近 5 期
datafinder describe 工业增加值.csv                       # 描述统计
datafinder nbs dbs / tree / indicators                    # 浏览分类树
```

输出是**整洁长表**（期别、地区、指标、数值、单位，每行一个观测值），CSV 带 BOM，Excel 直接打开不乱码；xlsx 含"说明"工作表（来源、取数时间、口径提示）。不指定路径时保存在 `~/.datafinder/exports/`。时间格式：年度 `2015`、月度 `201501`、季度 `202401`（2024 年第 1 季）。

## 统计年鉴目录

按"年鉴系列 × 年份版次"建立数据表清单：表号、标题、章节、**该版次的链接与文件格式**，并把不同年份的同一张表归并，告诉你"这张表有哪些年份可查"。

```bash
datafinder yearbook index                    # 抓目录页（一版次一个请求，串行限速；只存标题和链接，不下载表格）
datafinder yearbook list                     # 各系列收录了哪些年份
datafinder yearbook find "规模以上工业企业主要经济指标" -r CN-SD
```

目前收录：**中国统计年鉴 2005–2025（21 版，约 1.4 万张表）**，以及山东、辽宁、河南等省级年鉴（页面结构与统计出版社"zk"模板一致的）。北京、河北、四川、黑龙江的地址模板已登记，但从部分网络环境访问会超时或被拒绝，会如实标注、不重试不绕过，换网络后重跑同一命令即可。其余省份先只做导航入口。
表格文件的格式以目录页给出的为准：2005–2012 版多为网页表格，2013 版起多为图片，个别版次有 xls——**要可直接分析的数值请用国家统计局指标（`nbs find` + `nbs get`）**，年鉴目录负责告诉你"表在哪、哪年有"。

## 它查哪些来源

| 来源 | 说明 |
|---|---|
| 国家统计局 | 中国官方统计指标（搜索给全国级最新值；「中国统计」页可按分省/城市等数据库逐级取数）。**实验性**：新版发布库 2026-03 上线、试运行、接口无正式文档 |
| Zenodo、DataCite | 科研数据；DataCite 覆盖上千个仓库，含中国科学数据银行 ScienceDB。中文问句会**中英文各查一次**，以召回中文记录 |
| HDX、data.gov.uk、加拿大、澳大利亚 | 政府/人道数据（CKAN） |
| Dataseek（可选） | 约 60 个长尾国际来源，吃我们翻译好的英文检索词 |

单个来源失败、超时或限流时，其余来源照常返回，并在结果末尾标明每个来源的状态；**失败不会被当成"0 条结果"**。

## 它怎么理解中文

- **自研分词**：拉丁词 + 中文二元组，"人口""气象"这类两字词也能命中。
- **中英词表**（`src/datafinder/data/lexicon.zh-en.yaml`）：`人口普查 → population census`。中文问句会同时用中文词查中文来源、用英文词查英文来源，国际来源里的英文记录也能被中文问句召回。**词表加一行即生效。**
- **识别意图**：地区（`辽宁`、`美国`）、格式（`CSV`、`API`）、时间（`近十年`、`2010-2020`）、行业。
- **排序**：多来源 RRF 融合 × 概念覆盖度（IDF 加权，标题命中高于描述命中），中文问句时对官方、中文来源略加权；`--explain` 可查看每一项。

## 效果评估

`bench/` 里有 14 条中文找数问句和约 580 条相关性标注，`datafinder bench run` 在同一批检索结果上比较排序方案。当前完整引擎 nDCG@10 为 0.746；把中文原句直接交给 Dataseek 只有 0.242。方法、结果与**局限**（样本小、标注者是 AI）见 [bench/README.md](bench/README.md)。

## 扩展

| 想做什么 | 改哪里 |
|---|---|
| 新增 CKAN 类门户 | `src/datafinder/data/sources.yaml` 加一行 |
| 新增全新接口 | 写 `src/datafinder/sources/xxx.py`，在 `registry.py` 登记，再加一行 yaml |
| 新增/修正门户 | `src/datafinder/data/curated/portals.yaml` 加一条（新增须自己访问核验） |
| 新增中文词 / 行业 | `lexicon.zh-en.yaml` / `industries.yaml` |

## 国家统计局有 MCP 吗？

截至 2026-11-20 调研：**没有官方 MCP**。个人/社区项目有 [`icen-ai/mcp-cnbs`](https://github.com/icen-ai/mcp-cnbs)（Apache-2.0，`npx mcp-cnbs`，还聚合世界银行、IMF、OECD 等）、`Eliseowzy/cnbs-mcp-server`、`Ddhjx-code/national_data`（`national-stats-mcp`）、`pipeworx-io/mcp-china-macro`（含 70 城房价）。它们都是对同一个非公开网页接口的封装；DataFinder 直接调用接口，不依赖它们，你也可以把它们和 DataFinder 一起挂进自己的 AI 助手。省级开放平台（北京、浙江）需要自行申请密钥，上海的无条件开放数据可直接调用。

## 数据来源与署名

见 [NOTICE](NOTICE)。数据门户目录来自 [Dateno dataportals-registry](https://github.com/datenoio/dataportals-registry)（CC BY 4.0），本仓库不包含该数据，由 `sync` 在你本机下载。界面所列门户**未实时核验**；数据集的许可以原站为准，"许可未知"不代表可以随意使用。

## 开发

```bash
uv sync --extra dev
uv run pytest                                    # 单元测试（不联网）
uv run ruff check . && uv run ruff format --check .
uv run datafinder bench topics                   # 真实选题验收（需本机已建门户库/指标索引/年鉴目录）
uv run datafinder bench topics --holdout         # 留出集：估计没调过的新选题上的命中率
```

目录结构：

```
src/datafinder/
  cli/            命令行：app（主命令）· data（plan / nbs / describe / mcp）· yearbook
  mcp_server/     MCP 服务（stdio）
  service/        共用服务层（CLI 与 MCP 都走这里）：nbs · yearbook · plan · discovery · export · status
  nbs/            国家统计局：client（接口）· index（本地指标索引）
  yearbook/       统计年鉴：registry · parse · crawl · index
  portals/        门户目录：同步、分类、浏览、检索
  sources/        数据集来源（Zenodo、DataCite、CKAN、国家统计局搜索、Dataseek）
  engine/         并发检索、HTTP、缓存、去重、排序
  query/          分词、词表、检索计划、检索单元与统计术语同义词
  bench/          排序基准（ranking）与真实选题验收（topics）
  server/         本地网页
  data/           词表、行业、地区、精选门户、年鉴系列、术语同义词等 YAML
tests/            按模块分目录，与 src 一一对应；fixtures/ 放真实页面/接口响应的缩小样本
bench/            基准数据：问句、相关性标注、选题验收集（开发集 + 留出集）
```

## 许可

[MIT](LICENSE)
