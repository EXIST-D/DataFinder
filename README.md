# DataFinder

中文优先的**找数入口**：用关键词或一句话，快速找到各行业的**数据门户**、**数据集**、**数据表**，以及它们的**下载 / API 地址**。

> 状态：早期开发中（v0.0.x）。**已可用：门户检索**（4.2 万个数据门户，中文优先，含 44 个人工精选）。数据集与下载地址检索在开发中。

## 快速开始

需要 [uv](https://docs.astral.sh/uv/)。

```bash
git clone https://github.com/EXIST-D/DataFinder.git
cd DataFinder
uv sync

uv run datafinder sync                 # 首次：下载并建立本地门户库（约 7MB）
uv run datafinder portals "人口普查"
uv run datafinder portals "气象"
uv run datafinder portals "辽宁 开放数据"
uv run datafinder portals "中国各省近十年人口 GDP 城镇化率 CSV"
uv run datafinder portals "air quality" --country US
uv run datafinder portals --country CN --industry 气象气候 --api
uv run datafinder doctor               # 体检
```

命令名为 `datafinder`（短别名 `dfd`）。不使用 `df`，因为它会遮蔽系统自带的磁盘命令。

常用选项：`-n` 条数、`--country/-c` 国家、`--industry/-i` 行业、`--type/-t` 门户类型、`--api` 只看有 API 的、`--json` 机器可读、`--explain` 查看排序依据。

## 它怎么理解中文

- **自研分词**：拉丁词 + 中文二元组，所以"人口""气象"这类两字词也能命中（SQLite 自带的 trigram 分词做不到）。
- **中英词表**（`src/datafinder/data/lexicon.zh-en.yaml`）：`人口普查 → population census`，让中文查询也能命中英文门户。**加一行即生效。**
- **识别意图**：地区（`辽宁`、`美国`）、格式（`CSV`、`API`）、时间（`近十年`、`2010-2020`）、行业。
- **行业标签**：用规则与关键词给门户打 22 个行业标签（`industries.yaml`），可复现、可人工修正。

## 扩展

| 想做什么 | 改哪里 |
|---|---|
| 新增/修正门户 | `src/datafinder/data/curated/portals.yaml` 加一条（新增须自己访问核验） |
| 新增中文词 | `src/datafinder/data/lexicon.zh-en.yaml` 加一行 |
| 新增行业或关键词 | `src/datafinder/data/industries.yaml` |

## 数据来源与署名

- 数据门户目录：[Dateno dataportals-registry](https://github.com/datenoio/dataportals-registry)，数据 CC BY 4.0。本仓库**不包含**该数据，由 `sync` 在你本机下载。
  门户的 `status` 是上游人工策展，不是实时探测，界面所列门户**未实时核验**。
- 数据集长尾来源（后续、可选）：[Dataseek](https://github.com/lmarkmann/dataseek)，Apache-2.0 / MIT。

## 开发

```bash
uv sync --extra dev
uv run pytest
uv run ruff check . && uv run ruff format --check .
```

## 许可

[MIT](LICENSE)
