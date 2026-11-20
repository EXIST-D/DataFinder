# DataFinder

中文优先的**找数入口**：用关键词或自然语言，快速找到各行业的**数据门户**、**数据集**、**数据表**，以及它们的**下载 / API 地址**。

> 状态：早期开发中（v0.0.x）。目前只有骨架与 `doctor` 命令。

## 安装与体检

需要 [uv](https://docs.astral.sh/uv/)。

```bash
# 从源码运行
git clone https://github.com/EXIST-D/DataFinder.git
cd DataFinder
uv sync
uv run datafinder doctor
```

命令名为 `datafinder`（短别名 `dfd`）。不使用 `df`，因为它会遮蔽系统自带的磁盘命令。

## 数据来源与署名

- 数据门户目录：[Dateno dataportals-registry](https://github.com/datenoio/dataportals-registry)，CC BY 4.0。
- 数据集长尾来源（可选）：[Dataseek](https://github.com/lmarkmann/dataseek)，Apache-2.0 / MIT。

## 许可

[MIT](LICENSE)
