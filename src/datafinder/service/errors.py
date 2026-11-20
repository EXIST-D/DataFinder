"""服务层统一的、可直接展示给用户（或 AI 助手）的错误。"""

from __future__ import annotations


class ServiceError(Exception):
    """消息写给最终用户看，并告诉对方下一步怎么办。"""
