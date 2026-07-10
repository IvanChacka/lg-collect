from __future__ import annotations

import os
import sqlite3
from typing import Any

from dotenv import load_dotenv
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.sqlite import SqliteSaver

from core.graph import build_graph


def load_env() -> None:
    # 不覆盖进程已有环境变量：允许用 `FOO=bar python ...` 的方式临时覆盖配置，
    # 也避免把系统环境变量“意外改写”。开发期如需变更配置，推荐重启进程。
    load_dotenv(override=False)


def _sqlite_checkpointer(sqlite_path: str) -> SqliteSaver:
    os.makedirs(os.path.dirname(sqlite_path) or ".", exist_ok=True)
    conn = sqlite3.connect(sqlite_path, check_same_thread=False)
    return SqliteSaver(conn)


def build_app(*, use_sqlite: bool = True):
    load_env()

    graph = build_graph()

    checkpoint_path = os.getenv("CHECKPOINT_SQLITE_PATH", ".data/checkpoints.sqlite")
    if use_sqlite:
        checkpointer = _sqlite_checkpointer(checkpoint_path)
    else:
        checkpointer = InMemorySaver()

    return graph.compile(checkpointer=checkpointer, name="hot-collect")


def make_config(*, thread_id: str, **kwargs: Any) -> dict:
    configurable = {"thread_id": thread_id, **kwargs}
    return {"configurable": configurable}
