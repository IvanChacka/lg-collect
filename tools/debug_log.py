from __future__ import annotations

import os
import time
from typing import Any


def _debug_enabled(cfg: dict[str, Any] | None = None) -> bool:
    if cfg is not None and "llm_debug" in cfg:
        v = cfg.get("llm_debug")
        return str(v).strip().lower() in ("1", "true", "yes", "on", "debug")
    v = os.getenv("LLM_DEBUG", "0")
    return str(v).strip().lower() in ("1", "true", "yes", "on", "debug")


def _log_path(cfg: dict[str, Any] | None = None) -> str:
    if cfg is not None and cfg.get("llm_debug_log_path"):
        return str(cfg["llm_debug_log_path"])
    return os.getenv("LLM_DEBUG_LOG_PATH", ".data/llm_debug.log")


def debug_log(msg: str, *, cfg: dict[str, Any] | None = None, prefix: str = "llm") -> None:
    """
    轻量调试日志：
    - 仅在 LLM_DEBUG=1 时启用
    - 输出到 stderr，并追加写入到 .data/llm_debug.log（可通过 LLM_DEBUG_LOG_PATH 覆盖）
    - 绝不输出任何 API Key
    """

    if not _debug_enabled(cfg):
        return

    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] [{prefix}] {msg}"

    # stderr
    try:
        import sys

        print(line, file=sys.stderr)
    except Exception:
        pass

    # file
    try:
        path = _log_path(cfg)
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        return

