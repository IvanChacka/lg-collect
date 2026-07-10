from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from langchain_core.runnables import RunnableConfig

from core.state import HotCollectState
from tools.debug_log import debug_log
from tools.llm_client import llm_filter_articles
from tools.page_fetch import fetch_page
from tools.utils import get_config


def llm_filter_articles_node(
    state: HotCollectState, config: RunnableConfig | None = None
) -> dict[str, Any]:
    """
    节点 2：对每个词条的文章候选做 LLM 筛选，留下更有研究和扩展价值的链接。
    """

    cfg = get_config(config)
    keep_limit = int(cfg.get("article_llm_keep_limit") or 5)
    if keep_limit <= 0:
        raise ValueError(f"ARTICLE_LLM_KEEP_LIMIT 必须为正整数，但得到: {keep_limit!r}")

    results = state.get("article_search_results") or []
    if not results:
        return {"article_candidates": []}

    debug_log(
        f"node=llm_filter_articles start keywords={len({(r.get('keyword') or '').strip() for r in results if isinstance(r, dict)})} results={len(results)} keep={keep_limit}",
        cfg=cfg,
        prefix="node",
    )

    grouped: dict[str, list[dict[str, Any]]] = {}
    for result in results:
        keyword = (result.get("keyword") or "").strip() or "unknown"
        grouped.setdefault(keyword, []).append(result)

    candidates: list[dict[str, Any]] = []
    validation_errors: list[str] = []
    try:
        for keyword, articles in grouped.items():
            fetchable_articles = [a for a in articles if _is_fetchable_url(str(a.get("url") or ""))]
            if not fetchable_articles:
                hosts = sorted(
                    {
                        (urlparse(str(a.get("url") or "")).hostname or "").lower()
                        for a in articles
                        if str(a.get("url") or "").strip()
                    }
                )
                raise RuntimeError(
                    f"{keyword}: 搜索结果都来自不可抓取/高概率 403 的站点（hosts={hosts[:6]}），无法继续聚合 HTML"
                )
            debug_log(
                f"node=llm_filter_articles keyword={keyword!r} in={len(articles)} fetchable={len(fetchable_articles)}",
                cfg=cfg,
                prefix="node",
            )
            filtered = llm_filter_articles(
                keyword=keyword,
                articles=fetchable_articles,
                cfg=cfg,
                keep_limit=keep_limit,
            )
            for article in filtered:
                url = (article.get("url") or "").strip()
                if not url:
                    validation_errors.append(f"{keyword}: 缺少文章 URL")
                    continue
                title = (article.get("title") or "").strip() or "未命名文章"
                candidates.append({**article, "title": title, "selected": True})
    except BaseException as e:
        debug_log(
            f"node=llm_filter_articles exception={type(e).__name__}: {e!r}",
            cfg=cfg,
            prefix="node",
        )
        raise

    debug_log(
        f"node=llm_filter_articles done candidates={len(candidates)}",
        cfg=cfg,
        prefix="node",
    )

    if not candidates:
        detail = " | ".join(validation_errors[:5]) if validation_errors else "LLM 未筛出可抓取文章"
        raise RuntimeError(f"llm_filter_articles 无可用文章候选: {detail}")

    return {"article_candidates": candidates}


def _is_fetchable_url(url: str) -> bool:
    u = str(url or "").strip()
    if not u:
        return False
    try:
        host = (urlparse(u).hostname or "").lower()
    except Exception:
        return False
    if not host:
        return False
    blocked = (
        "bilibili.com",
        "douyin.com",
        "weibo.com",
    )
    return not any(host == d or host.endswith("." + d) for d in blocked)
