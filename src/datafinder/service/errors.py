"""服务层统一的错误：写给最终用户（或 AI 助手）看，带稳定的错误码与"下一步怎么办"。

错误码是机器可识别的稳定枚举；MCP 层把它们转成 ToolError（SDK 只会把 ToolError 的原文交给助手）。
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

CODES = (
    "INDEX_NOT_READY",
    "SOURCE_RATE_LIMITED",
    "SOURCE_ACCESS_BLOCKED",
    "SOURCE_TIMEOUT",
    "AUTH_REQUIRED",
    "INVALID_FACET_ID",
    "INVALID_ARGUMENT",
    "NOT_FOUND",
    "UNSUPPORTED",
    "DOWNLOAD_UNAVAILABLE",
    "CONFIRM_REQUIRED",
    "PARTIAL_RESULT",
    "ERROR",
)


class ServiceError(Exception):
    def __init__(
        self,
        message: str,
        *,
        code: str = "ERROR",
        retryable: bool = False,
        next_action: str | None = None,
    ):
        assert code in CODES, code
        super().__init__(message)
        self.code, self.retryable, self.next_action = code, retryable, next_action

    def to_dict(self) -> dict:
        return {
            "ok": False,
            "error": {
                "code": self.code,
                "message": str(self),
                "retryable": self.retryable,
                "next_action": self.next_action,
            },
        }

    def __str__(self) -> str:
        return super().__str__()


def translate(exc: BaseException) -> ServiceError:
    """把各层的异常翻译成 ServiceError；认不出的原样包成 ERROR（保留原消息）。"""
    from datafinder.catalog.adapters import AdapterError, Unsupported
    from datafinder.catalog.query import InvalidFacet
    from datafinder.catalog.tree import InvalidNode
    from datafinder.engine.errors import SourceError

    if isinstance(exc, ServiceError):
        return exc
    if isinstance(exc, InvalidFacet):
        return ServiceError(
            str(exc),
            code="INVALID_FACET_ID",
            next_action="先用 facets 查看合法的主题、地域和资源类型",
        )
    if isinstance(exc, InvalidNode):
        return ServiceError(
            str(exc),
            code="NOT_FOUND",
            next_action="从该轴的顶层（不带 node）逐级展开，使用返回的节点 id",
        )
    if isinstance(exc, AdapterError):
        return ServiceError(str(exc), code="INVALID_ARGUMENT")
    if isinstance(exc, Unsupported):
        return ServiceError(str(exc), code="UNSUPPORTED")
    if isinstance(exc, SourceError):
        if exc.kind == "rate_limited":
            return ServiceError(
                "来源的访问防护要求验证，已暂停访问（不会重试或绕过）。",
                code="SOURCE_RATE_LIMITED",
                retryable=True,
                next_action="等几分钟再试",
            )
        if exc.kind == "blocked":
            return ServiceError(
                f"来源拒绝了自动访问：{exc}",
                code="SOURCE_ACCESS_BLOCKED",
                next_action="用浏览器打开该来源的原站页面",
            )
        if exc.kind in ("timeout", "down"):
            return ServiceError(
                f"来源暂时连不上：{exc}",
                code="SOURCE_TIMEOUT",
                retryable=True,
                next_action="稍后重试",
            )
        return ServiceError(f"来源出错：{exc}")
    if isinstance(exc, TimeoutError):
        return ServiceError(
            "来源接口超时", code="SOURCE_TIMEOUT", retryable=True, next_action="稍后重试"
        )
    return ServiceError(str(exc))


@contextmanager
def guard() -> Iterator[None]:
    """把下层异常统一翻译成 ServiceError。"""
    try:
        yield
    except ServiceError:
        raise
    except Exception as exc:  # noqa: BLE001 —— 边界层：翻译而不吞掉
        raise translate(exc) from exc
