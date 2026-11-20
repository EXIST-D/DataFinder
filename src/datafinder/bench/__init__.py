"""基准：排序基准（ranking）与真实选题验收（topics）。命令：`datafinder bench pool|run|topics`。"""

from .ranking import (
    VARIANTS,
    BenchQuery,
    K,
    bench_app,
    grade_of,
    keys_of,
    load_judgments,
    load_queries,
    ndcg_at_k,
    precision_at,
)
from .topics import topics_app

# 把 topics 命令并进同一个 `bench` 命令组
for _cmd in topics_app.registered_commands:
    bench_app.registered_commands.append(_cmd)

__all__ = [
    "K",
    "VARIANTS",
    "BenchQuery",
    "bench_app",
    "grade_of",
    "keys_of",
    "load_judgments",
    "load_queries",
    "ndcg_at_k",
    "precision_at",
]
