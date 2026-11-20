# 贡献指南

DataFinder 的目标是**多领域、多来源的聚合检索，能下载就下载**。"中国官方统计"只是第一个领域包；
所有改动都要以"核心通用、领域插件化"为前提，不要把项目做成某一个领域的专用工具。

## 目录与分层

```
src/datafinder/
  catalog/ engine/ query/ portals/ sources/ tables.py …    核心（通用，不含任何领域专有名称）
  service/                                                  共用服务层（CLI / MCP / 网页都走这里）
  cli/  mcp_server/  server/                                入口层
  domains/<domain_id>/                                      领域包：适配器、分类词典与规则、术语词表、data/
    plugin.py                                               向核心注册扩展点（适配器、分类词典、术语词表、来源协议、限速、网页路由…）
tests/                                                      与 src 一一对应；tests/architecture 守护分层
bench/                                                      基准数据：排序问句、选题验收集、分面检索验收集（各有开发集 + 留出集）
```

## 红线（`tests/architecture/test_layering.py` 自动检查）

1. **核心不得 import 具体领域包的子模块**，只能经 `datafinder.domains` 加载器；领域包可以依赖核心，反过来不行。
2. **核心代码里不得出现领域专有标识符**（国家统计局、年鉴等的专有名称）。
3. 没有白名单：任何违规都直接失败。

## 新增一个来源 / 领域

- 已有协议的站点（如 CKAN 实时检索）：在 `data/sources.yaml`（或领域包的 `data/sources.yaml`）加一行。
- 全新来源要进**统一目录**：在领域包里实现 `catalog.adapters.Adapter`（按需实现 `discover` 抓目录 / `fetch` 实时取数 / `migrate`），
  把来源翻译成 `Entry`（资源 + 主题 + 地域 + 分发入口），在 `plugin.py` 里 `register_adapter(...)`。
  文件直链的下载是通用能力，适配器不用管；获取方式（download / generate / link）由分发入口推出，不存储。
- 新领域：建 `domains/<id>/`（含 `plugin.py`、`taxonomy.yaml`、`topic_rules.yaml`、`data/`），在 `domains/__init__.py` 的 `DOMAINS` 里登记；
  先写**选题验收集与分面检索验收集**（开发集 + 留出集，各 ≥ 8 道），再写适配器。
- 术语词表只写"概念 → 该领域里实际使用的名称"的通用对应，不为某个选题定制；加条目前先确认名称真实存在。

## 工程原则

- **一个概念只有一份实现**：存储 schema、检索打分、导出、错误枚举各一份；发现重复就合并，不要并存。
- **MCP 工具按能力命名**（search / fetch / catalog…），不带领域词；领域用参数区分。
- **诚实**：失败不当作"0 条结果"；未知就是未知；不编造链接和数值；每条结果标注获取方式与核验状态。
- **礼貌**：访问官方站点一律串行、限速、缓存，遇防护即停，不重试不绕过；索引由用户主动触发。
- **下载只在用户明确要求时发生**；搜索、找数方案、检索类 MCP 工具永不下载。
- **测试全部离线**：HTTP 用 `httpx.MockTransport`，站点响应用 `tests/fixtures/` 里的缩小样本。

## 开发

```bash
uv sync --extra dev
uv run pytest
uv run ruff check . && uv run ruff format --check .
uv run datafinder bench topics [--holdout]   # 选题验收（需本机已建资源目录）
uv run datafinder bench facets [--holdout]   # 分面检索验收（含"必须不出现"的误导项）
```

提交信息格式：`DF-<序号>: 描述`。
