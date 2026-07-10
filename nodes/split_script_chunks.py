from __future__ import annotations

import re
from typing import Any

from langchain_core.runnables import RunnableConfig

from core.state import HotCollectState
from tools.utils import get_config


def _split_paragraphs(text: str) -> list[str]:
    t = (text or "").strip()
    if not t:
        return []
    parts = [p.strip() for p in re.split(r"\n\s*\n+", t) if p.strip()]
    if parts:
        return parts
    # 极端情况下：全是单行，退化为按换行切
    return [p.strip() for p in t.splitlines() if p.strip()]


def _chunk_by_limit(parts: list[str], *, max_chars: int) -> list[str]:
    if not parts:
        return []
    if max_chars <= 0:
        return ["\n\n".join(parts)]

    chunks: list[str] = []
    buf: list[str] = []
    buf_len = 0
    for p in parts:
        p = p.strip()
        if not p:
            continue
        extra = len(p) + (2 if buf else 0)
        if buf and buf_len + extra > max_chars:
            chunks.append("\n\n".join(buf).strip())
            buf = [p]
            buf_len = len(p)
        else:
            buf.append(p)
            buf_len += extra
    if buf:
        chunks.append("\n\n".join(buf).strip())
    return [c for c in chunks if c]


def build_script_chunks(*, script: str, max_chars: int) -> list[dict[str, Any]]:
    """
    将口播文案切分为多个 chunk（用于后续按 chunk 并行做分镜切段）。
    返回结构与 state['script_chunks'] 一致。
    """

    parts = _split_paragraphs(script)
    chunks = _chunk_by_limit(parts, max_chars=max_chars)
    return [{"chunk_index": i, "text": c} for i, c in enumerate(chunks) if str(c or "").strip()]


def split_script_chunks_node(
    state: HotCollectState, config: RunnableConfig | None = None
) -> dict[str, Any]:
    """
    节点：把“口播文案”按自然段/语义段切分成多个 chunk，避免后续一次性 JSON 太长导致效果变差。
    下游会对每个 chunk 分别做“分镜切段 -> 加画面提示 -> 合并”。
    """

    cfg = get_config(config)
    max_chars = int(cfg.get("storyboard_chunk_max_chars") or 1200)

    script = str(state.get("final_script_markdown") or "").strip()
    if not script:
        raise RuntimeError("split_script_chunks 口播文案为空：final_script_markdown 为空")

    chunks = build_script_chunks(script=script, max_chars=max_chars)
    if not chunks:
        raise RuntimeError("split_script_chunks 未能切分出任何 chunk")

    return {
        "script_chunks": chunks,
        "status": "completed",
    }
