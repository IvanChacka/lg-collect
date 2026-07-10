from __future__ import annotations

from typing import Any

from core.state import Material


def collect_materials_for_keywords(
    *, keywords: list[str], cfg: dict[str, Any], per_keyword: int = 5
) -> list[Material]:
    provider = (cfg.get("search_provider") or "").lower().strip()
    raise NotImplementedError(
        "tools/search.py 已移除 mock 占位实现；请改用 nodes/search_articles.py / nodes/search_videos.py + tools/search_engines.py"
        f"（当前 SEARCH_PROVIDER={provider!r}）"
    )
