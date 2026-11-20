"""`datafinder doctor`：一键体检，只做轻量探测，不抓取数据。"""

from __future__ import annotations

import platform
import subprocess
from dataclasses import dataclass
from typing import Literal

import httpx

from . import __version__
from .config import USER_AGENT, data_dir
from .sources.dataseek import find_executable
from .store import connect, db_path, get_meta, portal_count

Status = Literal["ok", "warn", "fail"]

# 门户库的数据来源（后续 `sync` 会用到），先探测其可达性
REGISTRY_PROBE_URL = (
    "https://raw.githubusercontent.com/datenoio/dataportals-registry/main/README.md"
)


@dataclass(frozen=True)
class Check:
    name: str
    status: Status
    detail: str


def check_python() -> Check:
    version = platform.python_version()
    return Check("Python", "ok", f"{version}（{platform.system()} {platform.machine()}）")


def check_data_dir() -> Check:
    path = data_dir()
    if path.exists():
        return Check("数据目录", "ok", str(path))
    return Check("数据目录", "ok", f"{path}（尚未创建，首次 sync 时自动创建）")


def check_portals() -> Check:
    path = db_path()
    if not path.exists():
        return Check("门户库", "warn", "尚未同步，请运行：datafinder sync")
    conn = connect(path)
    try:
        count = portal_count(conn)
        if count == 0:
            return Check("门户库", "warn", "为空，请运行：datafinder sync")
        synced = (get_meta(conn, "synced_at") or "未知")[:10]
        ref = get_meta(conn, "registry_ref") or "未知"
        return Check("门户库", "ok", f"{count} 条（Dateno {ref}，同步于 {synced}）")
    finally:
        conn.close()


def check_network(timeout: float = 8.0) -> Check:
    try:
        resp = httpx.head(
            REGISTRY_PROBE_URL,
            headers={"User-Agent": USER_AGENT},
            timeout=timeout,
            follow_redirects=True,
        )
    except httpx.HTTPError as exc:
        return Check("网络", "fail", f"无法访问 GitHub：{type(exc).__name__}")
    if resp.status_code >= 400:
        return Check("网络", "warn", f"门户库来源返回 HTTP {resp.status_code}")
    return Check("网络", "ok", "可访问门户库来源（Dateno 注册表）")


def check_dataseek(timeout: float = 10.0) -> Check:
    """Dataseek 是可选的长尾来源桥接；没装不算故障。"""
    exe = find_executable()  # 与搜索用同一个查找逻辑：uv tool 装的 dsk 不在 PATH 上
    if exe is None:
        return Check("Dataseek 桥接", "warn", "未安装（可选，用于覆盖长尾来源）")
    try:
        out = subprocess.run(  # noqa: S603 参数为固定数组，不拼 shell
            [exe, "--version"], capture_output=True, text=True, timeout=timeout, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return Check("Dataseek 桥接", "warn", f"无法运行：{type(exc).__name__}")
    return Check("Dataseek 桥接", "ok", out.stdout.strip() or exe)


def run_checks(*, offline: bool = False) -> list[Check]:
    checks = [
        Check("DataFinder", "ok", __version__),
        check_python(),
        check_data_dir(),
        check_portals(),
    ]
    if not offline:
        checks.append(check_network())
    checks.append(check_dataseek())
    return checks
