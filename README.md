# DataFinder

中文优先的**找数入口**：用关键词或一句话，快速找到各行业的**数据门户**、**数据集**、**数据表**，以及它们的**下载 / API 地址**。

> 状态：早期开发中（v0.0.x）。**已可用：门户检索 + 数据集检索**（命令行）。本地网页与 MCP 在开发计划中。

## 快速开始

需要 [uv](https://docs.astral.sh/uv/)。

```bash
git clone https://github.com/EXIST-D/DataFinder.git
cd DataFinder
uv sync

uv run datafinder sync                      # 首次：下载并建立本地门户库（约 7MB）

# 找数据集（一次查多个来源，合并去重）
uv run datafinder search "中国各省近十年人口 GDP 城镇化率 CSV"
uv run datafinder search "国内生产总值" -r  # -r 同时显示下载/API 资源
uv run datafinder search "air quality" -n 5

# 找数据门户（哪些网站可能有这类数据）
uv run datafinder portals "气象"
uv run datafinder portals "辽宁 开放数据"

uv run datafinder sources                   # 有哪些来源、状态如何
uv run datafinder doctor                    # 体检
```

命令名为 `datafinder`（短别名 `dfd`）。不使用 `df`，因为它会遮蔽系统自带的磁盘命令。

`search` 常用选项：`-n` 条数、`-r` 显示资源、`-s/-x` 指定/排除来源、`--refresh` 忽略缓存、`--json` 机器可读、`--explain` 查看查询计划与排序依据。

## 它查哪些来源

| 来源 | 说明 |
|---|---|
| 国家统计局 | 中国官方统计指标（全国级，最新值 + 可直接取时间序列的 API）。**实验性**：新版发布库 2026-03 上线、试运行、接口无正式文档 |
| Zenodo、DataCite | 科研数据；DataCite 覆盖上千个仓库，含中国科学数据银行 ScienceDB |
| HDX、data.gov.uk、加拿大、澳大利亚 | 政府/人道数据（CKAN） |
| Dataseek（可选） | 约 60 个长尾国际来源，吃我们翻译好的英文检索词 |

单个来源失败、超时或限流时，其余来源照常返回，并在结果末尾标明每个来源的状态；**失败不会被当成"0 条结果"**。

## 它怎么理解中文

- **自研分词**：拉丁词 + 中文二元组，"人口""气象"这类两字词也能命中。
- **中英词表**（`src/datafinder/data/lexicon.zh-en.yaml`）：`人口普查 → population census`。中文问句会同时用中文词查中文来源、用英文词查英文来源，国际来源里的英文记录也能被中文问句召回。**词表加一行即生效。**
- **识别意图**：地区（`辽宁`、`美国`）、格式（`CSV`、`API`）、时间（`近十年`、`2010-2020`）、行业。
- **排序**：多来源 RRF 融合 × 概念覆盖度（IDF 加权，标题命中高于描述命中），中文问句时对官方、中文来源略加权；`--explain` 可查看每一项。

## 扩展

| 想做什么 | 改哪里 |
|---|---|
| 新增 CKAN 类门户 | `src/datafinder/data/sources.yaml` 加一行 |
| 新增全新接口 | 写 `src/datafinder/sources/xxx.py`，在 `registry.py` 登记，再加一行 yaml |
| 新增/修正门户 | `src/datafinder/data/curated/portals.yaml` 加一条（新增须自己访问核验） |
| 新增中文词 / 行业 | `lexicon.zh-en.yaml` / `industries.yaml` |

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
