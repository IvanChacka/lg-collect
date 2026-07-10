from __future__ import annotations

from core.state import HotItem


def merge_hot_items(*, weibo: list[HotItem], douyin: list[HotItem], limit: int = 50) -> list[HotItem]:
    """
    合并多平台热点榜单，做简单去重与重新排序。
    - 先按平台内 rank 保留
    - 再按“交替取样”合并，避免单平台占满
    """

    weibo_sorted = sorted(weibo, key=lambda x: int(x.get("rank", 0)))
    douyin_sorted = sorted(douyin, key=lambda x: int(x.get("rank", 0)))

    merged: list[HotItem] = []
    seen: set[str] = set()
    i = 0
    while len(merged) < limit and (i < len(weibo_sorted) or i < len(douyin_sorted)):
        for src in (weibo_sorted, douyin_sorted):
            if i >= len(src):
                continue
            kw = src[i].get("keyword") or ""
            if not kw or kw in seen:
                continue
            seen.add(kw)
            merged.append(src[i])
            if len(merged) >= limit:
                break
        i += 1

    # 重新编号 rank（仅用于下游展示/排序）
    out: list[HotItem] = []
    for idx, it in enumerate(merged, start=1):
        out.append({**it, "rank": idx})
    return out

