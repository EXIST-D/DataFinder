# DataFinder

中文优先的**找数入口**：用关键词或一句话，快速找到各行业的**数据门户**、**数据集**、**数据表**，以及它们的**下载 / API 地址**。

> 状态：早期开发中（v0.0.x）。**已可用：门户检索 + 数据集检索（命令行 + 本地网页）**。MCP 在开发计划中。

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
uv run pytest
uv run ruff check . && uv run ruff format --check .
```

## 许可

[MIT](LICENSE)
