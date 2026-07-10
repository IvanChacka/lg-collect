from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from langchain_core.runnables import RunnableConfig

from core.state import HotCollectState
from tools.llm_client import llm_add_visual_prompts
from tools.utils import get_config


def storyboard_visuals_node(
    state: HotCollectState, config: RunnableConfig | None = None
) -> dict[str, Any]:
    """
    节点：在分镜切段结果上，为每段补充画面提示，最终产出整份 storyboard JSON。
    """

    cfg = get_config(config)
    segments = state.get("storyboard_segments") or []
    if not segments:
        raise RuntimeError("storyboard_visuals 缺少 storyboard_segments：请先运行 storyboard_segments")

    proposal_id = str(state.get("selected_proposal_id") or "").strip()
    proposals = {str(p.get("proposal_id") or "").strip(): p for p in (state.get("proposals") or [])}
    title = str((proposals.get(proposal_id) or {}).get("title") or "").strip()
    if not title:
        title = "未命名视频"

    max_workers = int(cfg.get("storyboard_workers") or 4)
    max_workers = max(1, min(max_workers, 8))

    def _batches(xs: list[dict[str, Any]], *, batch_size: int) -> list[list[dict[str, Any]]]:
        if batch_size <= 0:
            return [xs]
        out: list[list[dict[str, Any]]] = []
        buf: list[dict[str, Any]] = []
        for x in xs:
            buf.append(x)
            if len(buf) >= batch_size:
                out.append(buf)
                buf = []
        if buf:
            out.append(buf)
        return out

    # segments 过多时，单次 JSON 输出容易超长被截断。
    # 这里按 batch 分批补全画面提示（不做静默兜底，逐批严格校验）。
    usable = [
        {"spoken_text": str((s or {}).get("spoken_text") or "").strip()}
        for s in segments
        if str((s or {}).get("spoken_text") or "").strip()
    ]
    if not usable:
        raise RuntimeError("storyboard_visuals storyboard_segments 为空或格式不正确")

    batch_size = int(cfg.get("storyboard_visuals_batch_size") or 4)
    batch_size = max(1, min(batch_size, 8))
    batches = _batches(usable, batch_size=batch_size)

    results: list[list[dict[str, Any]] | None] = [None] * len(batches)

    def _run_one(i: int, batch: list[dict[str, Any]]) -> tuple[int, list[dict[str, Any]]]:
        enriched = llm_add_visual_prompts(title=title, segments=batch, cfg=cfg)
        return i, enriched

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futs = [ex.submit(_run_one, i, batch) for i, batch in enumerate(batches)]
        for fut in as_completed(futs):
            i, payload = fut.result()
            results[i] = payload

    if any(r is None for r in results):
        raise RuntimeError("storyboard_visuals 存在未完成的 batch（线程池异常）")

    merged: list[dict[str, Any]] = []
    global_index = 1
    for r in results:
        assert r is not None
        for seg in r:
            spoken = str((seg or {}).get("spoken_text") or "").strip()
            visual = str((seg or {}).get("visual_prompt") or "").strip()
            if not spoken or not visual:
                raise RuntimeError("storyboard_visuals 出现空 spoken_text 或 visual_prompt（校验失败）")
            merged.append({"index": global_index, "spoken_text": spoken, "visual_prompt": visual})
            global_index += 1

    if not merged:
        raise RuntimeError("storyboard_visuals 未产出任何分镜段落")

    storyboard = {"title": title, "segments": merged}
    return {
        "storyboard": storyboard,
        "storyboard_json": json.dumps(storyboard, ensure_ascii=False, indent=2),
        "status": "completed",
    }
