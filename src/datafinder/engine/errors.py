"""来源错误的类型化。关键区别：来源"挂了"（可休息、可降级）与"接口变了/请求错了"（不休息）。"""

from __future__ import annotations

from typing import Literal

ErrorKind = Literal["down", "timeout", "rate_limited", "blocked", "shape", "http"]

# 这些类型表示来源当前不可用：10 分钟内不再打它，并可降级用过期缓存
_OUTAGE = {"down", "timeout", "rate_limited", "blocked"}


class SourceError(Exception):
    def __init__(self, kind: ErrorKind, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.kind = kind
        self.status = status

    @property
    def is_outage(self) -> bool:
        return self.kind in _OUTAGE or (self.status is not None and self.status >= 500)


class ShapeError(SourceError):
    """响应结构与预期不符。必须报错，**绝不能当成"0 条结果"**。"""

    def __init__(self, message: str) -> None:
        super().__init__("shape", message)
