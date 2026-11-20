"""基准：排序基准（ranking）、真实选题验收（topics）、分面检索验收（facets）。命令：`datafinder bench pool|run|topics|facets`。"""

from .facets import facets_app
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
for _cmd in [*topics_app.registered_commands, *facets_app.registered_commands]:
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
