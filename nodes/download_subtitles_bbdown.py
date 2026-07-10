from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from langchain_core.runnables import RunnableConfig

from core.state import HotCollectState
from tools.debug_log import debug_log
from tools.utils import get_config
from tools.video_asset_store import find_existing_subtitle, iter_video_items, subtitle_tool_result
from tools.video_assets import download_subtitle_only


def download_subtitles_bbdown_node(
    state: HotCollectState, config: RunnableConfig | None = None
) -> dict[str, Any]:
    """
    节点：使用 BBDown 尝试下载字幕（不下载音频）。

    输出：
    - video_assets：当前工具实际尝试过的视频字幕结果。
      已经有字幕的目录会跳过，不出现在本节点输出结果中。
    """

    cfg = get_config(config)
    thread_id = state.get("thread_id") or "thread"
    out_root = os.path.join(".data", "video_assets", thread_id)
    os.makedirs(out_root, exist_ok=True)

    items = iter_video_items(
        candidates=list(state.get("video_candidates") or []),
        assets=list(state.get("video_assets") or []),
        out_root=out_root,
    )
    if not items:
        return {"video_assets": []}

    fail_policy = str(cfg.get("video_download_fail_policy") or "partial").strip().lower()
    concurrency = int(cfg.get("video_download_concurrency") or 3)
    if concurrency <= 0:
        raise ValueError("VIDEO_DOWNLOAD_CONCURRENCY 必须为正整数")

    results: list[dict[str, Any]] = []
    errors: list[str] = []

    def _task(item: dict[str, str]) -> dict[str, Any] | None:
        exists = find_existing_subtitle(out_dir=item["out_dir"])
        if exists.ok and exists.subtitle_path:
            return None
        debug_log(
            f"download_subtitles_bbdown start keyword={item['keyword']!r} title={item['title']!r} url={item['url']!r}",
            cfg=cfg,
            prefix="node",
        )
        asset = download_subtitle_only(
            url=item["url"],
            title=item["title"],
            keyword=item["keyword"],
            out_root=out_root,
            cfg=cfg,
        )
        subtitle_path = str(asset.get("subtitle_path") or "").strip()
        debug_log(
            f"download_subtitles_bbdown done keyword={item['keyword']!r} subtitle={subtitle_path!r}",
            cfg=cfg,
            prefix="node",
        )
        return subtitle_tool_result(
            item=item,
            subtitle_downloaded=bool(subtitle_path),
            reason=str(asset.get("subtitle_error") or "").strip() or "no_srt",
        )

    with ThreadPoolExecutor(max_workers=min(concurrency, max(1, len(items)))) as ex:
        fut_map = {ex.submit(_task, item): item for item in items}
        for fut in as_completed(fut_map):
            item = fut_map[fut]
            try:
                result = fut.result()
                if result is not None:
                    results.append(result)
            except Exception as e:
                msg = f"{item['title']} <{item['url']}> 字幕下载失败(BBDown): {e!r}"
                errors.append(msg)
                results.append(subtitle_tool_result(item=item, subtitle_downloaded=False, reason=repr(e)))
                debug_log(
                    f"download_subtitles_bbdown error keyword={item['keyword']!r} title={item['title']!r} error={e!r}",
                    cfg=cfg,
                    prefix="node",
                )

    if errors and not results and fail_policy in {"raise", "strict", "fail"}:
        raise RuntimeError("download_subtitles_bbdown 全部失败: " + " | ".join(errors[:5]))

    return {"video_assets": results}
