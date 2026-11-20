"""架构分层守护：核心通用、领域插件化。

规则（见 CONTRIBUTING.md）：
1. 核心代码不得 import 具体领域包的子模块，只能用加载器 `datafinder.domains`；
2. 核心代码里不得出现领域专有标识符（国家统计局/年鉴的专有名称）；
3. 领域包可以依赖核心，反过来不行。

没有白名单：迁移期的白名单已在统一目录落地后清空，之后任何违规都直接失败。
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

SRC = Path(__file__).parents[2] / "src" / "datafinder"
DOMAIN_PREFIX = "datafinder.domains."
DOMAIN_MARKERS = re.compile(r"nbs|yearbook|cnstat|statsgov|stats\.gov", re.IGNORECASE)


def core_files() -> list[Path]:
    return sorted(p for p in SRC.rglob("*.py") if "domains" not in p.relative_to(SRC).parts)


def module_name(path: Path) -> str:
    parts = list(path.relative_to(SRC.parent).with_suffix("").parts)
    return ".".join(parts[:-1] if parts[-1] == "__init__" else parts)


def imported_modules(path: Path) -> set[str]:
    """文件 import 的所有模块的绝对名（把相对导入解析成绝对）。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    package = (
        module_name(path).rsplit(".", 1)[0] if path.name != "__init__.py" else module_name(path)
    )
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                anchor = package.split(".")
                anchor = anchor[: len(anchor) - (node.level - 1)]
                base = ".".join([*anchor, base] if base else anchor)
            out.add(base)
            out |= {f"{base}.{a.name}" for a in node.names}
    return out


def domain_imports(path: Path) -> set[str]:
    return {m for m in imported_modules(path) if m.startswith(DOMAIN_PREFIX)}


def marker_hits(path: Path) -> set[str]:
    """核心文件里的领域专有标识符（标识符与字符串常量；注释和文档字符串不算）。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings = {
        id(n.body[0].value)
        for n in ast.walk(tree)
        if isinstance(n, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef)
        and n.body
        and isinstance(n.body[0], ast.Expr)
        and isinstance(n.body[0].value, ast.Constant)
    }
    hits: set[str] = set()
    for node in ast.walk(tree):
        names: list[str] = []
        if isinstance(node, ast.Name):
            names = [node.id]
        elif isinstance(node, ast.Attribute):
            names = [node.attr]
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            names = [node.name]
        elif isinstance(node, ast.arg):
            names = [node.arg]
        elif isinstance(node, ast.alias):
            names = [node.asname or node.name]
        elif (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in docstrings
        ):
            names = [node.value]
        hits |= {n for n in names if DOMAIN_MARKERS.search(n)}
    return hits


def rel(path: Path) -> str:
    return path.relative_to(SRC).as_posix()


def test_core_does_not_import_domain_submodules():
    offenders = {rel(p): sorted(domain_imports(p)) for p in core_files() if domain_imports(p)}
    assert not offenders, (
        f"核心代码不得直接 import 领域包子模块（请经 datafinder.domains 加载器/注册扩展点）：{offenders}"
    )


def test_core_has_no_domain_specific_identifiers():
    offenders = {rel(p): sorted(marker_hits(p)) for p in core_files() if marker_hits(p)}
    assert not offenders, f"核心代码里出现领域专有标识符，应放进领域包：{offenders}"


def test_domains_are_registered_and_have_plugins():
    from datafinder import domains

    for d in domains.DOMAINS:
        assert (SRC / "domains" / d / "plugin.py").is_file(), f"领域包 {d} 缺少 plugin.py"
    assert {
        p.name for p in (SRC / "domains").iterdir() if p.is_dir() and not p.name.startswith("_")
    } == set(domains.DOMAINS)
