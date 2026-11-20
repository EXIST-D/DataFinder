"""命令行入口。命令名：`datafinder`（短别名 `dfd`）。"""

from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

from . import __version__
from .doctor import run_checks

app = typer.Typer(
    help="DataFinder：中文优先的找数入口。",
    no_args_is_help=True,
    add_completion=False,
)
console = Console()

_ICON = {"ok": "[green]✓[/green]", "warn": "[yellow]![/yellow]", "fail": "[red]✗[/red]"}


def _version_callback(value: bool) -> None:
    if value:
        console.print(f"datafinder {__version__}")
        raise typer.Exit()


@app.callback()
def _root(
    version: bool = typer.Option(
        False, "--version", "-V", callback=_version_callback, is_eager=True, help="显示版本"
    ),
) -> None:
    """DataFinder：中文优先的找数入口。"""


@app.command()
def doctor(
    offline: bool = typer.Option(False, "--offline", help="跳过需要联网的检查"),
) -> None:
    """一键体检：环境、网络、可选组件是否就绪。"""
    table = Table(show_header=False, box=None, pad_edge=False)
    checks = run_checks(offline=offline)
    for c in checks:
        table.add_row(_ICON[c.status], f"[bold]{c.name}[/bold]", c.detail)
    console.print(table)
    if any(c.status == "fail" for c in checks):
        raise typer.Exit(code=1)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
