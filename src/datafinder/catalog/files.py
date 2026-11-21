"""文件与网页的通用小工具：按扩展名识别文件格式与可用性、网页字节解码。年鉴、栏目列表等适配器共用。"""

from __future__ import annotations

from .entities import Distribution

# 扩展名 → (file_format, usability)。图片可以"下载"，但内容不能直接分析（usability=image）。
FORMATS: dict[str, tuple[str, str | None]] = {
    "xls": ("xls", "tabular"),
    "xlsx": ("xlsx", "tabular"),
    "csv": ("csv", "tabular"),
    "htm": ("html", "html_table"),
    "html": ("html", "html_table"),
    "jpg": ("jpg", "image"),
    "jpeg": ("jpg", "image"),
    "png": ("png", "image"),
    "gif": ("gif", "image"),
    "pdf": ("pdf", "document"),
    "doc": ("doc", "document"),
    "docx": ("docx", "document"),
    # 压缩包：里面是什么要解压才知道，不冒充"可直接分析"
    "zip": ("zip", None),
    "rar": ("rar", None),
}


def fmt_of(url: str) -> str:
    """链接指向的文件格式（file_format）；认不出返回扩展名或 other。"""
    path = url.split("?")[0].split("#")[0]
    name = path.rsplit("/", 1)[-1]
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    return FORMATS[ext][0] if ext in FORMATS else (ext or "other")


def distribution_for(url: str, fmt: str | None = None) -> Distribution:
    """文件直链 → 可下载的分发入口；认不出的格式只当页面链接，不冒充文件。"""
    fmt = fmt or fmt_of(url)
    for file_format, usability in FORMATS.values():
        if file_format == fmt:
            return Distribution("direct_file", url, file_format, usability)
    return Distribution("page", url, note="链接格式不是常见文件，请在浏览器打开")


def decode_html(content: bytes) -> str:
    """网页字节 → 文本：先试 UTF-8，再试 GB18030（老站点常见）。"""
    for enc in ("utf-8", "gb18030"):
        try:
            return content.decode(enc)
        except UnicodeDecodeError:
            continue
    return content.decode("utf-8", errors="replace")
