from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from langchain_core.runnables import RunnableConfig

from core.state import HotCollectState
from tools.debug_log import debug_log
from tools.llm_client import llm_filter_articles, llm_summarize_hot_event_from_html
from tools.page_fetch import fetch_page
from tools.utils import get_config


def _clip(text: str, max_chars: int) -> str:
    if max_chars <= 0:
        return text
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n\n...(truncated)...\n"


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
    blocked = ("bilibili.com", "douyin.com", "weibo.com")
    return not any(host == domain or host.endswith("." + domain) for domain in blocked)


def _group_articles_by_keyword(results: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for result in results:
        keyword = (result.get("keyword") or "").strip() or "unknown"
        grouped.setdefault(keyword, []).append(result)
    return grouped


def aggregate_article_text_node(
    state: HotCollectState, config: RunnableConfig | None = None
) -> dict[str, Any]:
    """
    节点：从文章搜索结果中筛选可抓取文章、聚合正文，并让 LLM 产出“热点详细事件总结”。
    """

    cfg = get_config(config)
    max_chars = int(cfg.get("article_aggregate_max_chars") or 40000)
    per_doc_max_chars = int(cfg.get("article_doc_max_chars") or 6000)
    if max_chars > 0:
        per_doc_max_chars = min(per_doc_max_chars, max_chars // 5)
    keep_limit = int(cfg.get("article_llm_keep_limit") or 5)
    if keep_limit <= 0:
        raise ValueError(f"ARTICLE_LLM_KEEP_LIMIT 必须为正整数，但得到: {keep_limit!r}")
    target_docs = max(1, min(5, keep_limit))

    results = [x for x in (state.get("article_search_results") or []) if isinstance(x, dict)]
    if not results:
        out: dict[str, Any] = {"article_analysis": ""}
        if state.get("selected_proposal_id"):
            out["materials_barrier"] = "articles"
        return out

    debug_log(
        f"node=aggregate_article_text start keywords={len({(r.get('keyword') or '').strip() for r in results})} results={len(results)} keep={keep_limit}",
        cfg=cfg,
        prefix="node",
    )

    grouped = _group_articles_by_keyword(results)
    pages: list[dict[str, Any]] = []
    extracts: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    fetch_errors: list[str] = []

    try:
        for keyword, articles in grouped.items():
            fetchable_articles = [article for article in articles if _is_fetchable_url(str(article.get("url") or ""))]
            if not fetchable_articles:
                hosts = sorted(
                    {
                        (urlparse(str(article.get("url") or "")).hostname or "").lower()
                        for article in articles
                        if str(article.get("url") or "").strip()
                    }
                )
                raise RuntimeError(
                    f"{keyword}: 搜索结果都来自不可抓取/高概率 403 的站点（hosts={hosts[:6]}），无法继续聚合 HTML"
                )
            debug_log(
                f"node=aggregate_article_text keyword={keyword!r} in={len(articles)} fetchable={len(fetchable_articles)}",
                cfg=cfg,
                prefix="node",
            )
            filtered = llm_filter_articles(
                keyword=keyword,
                articles=fetchable_articles,
                cfg=cfg,
                keep_limit=keep_limit,
            )
            debug_log(
                f"node=aggregate_article_text keyword={keyword!r} selected={len(filtered)}",
                cfg=cfg,
                prefix="node",
            )
            for article in filtered:
                if len(extracts) >= target_docs:
                    break

                i = len(pages) + 1
                url = str(article.get("url") or "").strip()
                if not url:
                    fetch_errors.append(f"[{i}] {keyword}: 缺少文章 URL")
                    continue

                title = str(article.get("title") or "").strip() or "未命名文章"
                try:
                    page = fetch_page(url=url, cfg=cfg)
                except Exception as exc:
                    pages.append({"url": url, "html": "", "error": repr(exc)})
                    fetch_errors.append(f"[{i}] {keyword}: {url} 抓取失败: {exc!r}")
                    continue

                pages.append(page)
                html = str(page.get("html") or "")
                page_error = str(page.get("error") or "").strip()
                if page_error:
                    fetch_errors.append(f"[{i}] {keyword}: {url} 抓取失败: {page_error}")
                    continue
                if not html:
                    fetch_errors.append(f"[{i}] {keyword}: {url} 抓取失败: HTML 为空")
                    continue

                clipped_html = _clip(html, per_doc_max_chars)
                candidates.append(
                    {
                        **article,
                        "title": title,
                        "selected": True,
                        "prefetched_html": html,
                    }
                )
                extracts.append({"keyword": keyword, "url": url, "title": title, "html": clipped_html})

            # 如果 LLM 筛选的文章全部抓取失败，回退到未被选中的备选文章
            if not extracts and len(filtered) > 0:
                filtered_urls = {str(a.get("url") or "").strip() for a in filtered}
                fallback_articles = [
                    a for a in fetchable_articles
                    if str(a.get("url") or "").strip() not in filtered_urls
                ]
                for article in fallback_articles:
                    if len(extracts) >= target_docs:
                        break
                    i = len(pages) + 1
                    url = str(article.get("url") or "").strip()
                    if not url:
                        fetch_errors.append(f"[{i}] {keyword}: 备选文章缺少 URL")
                        continue
                    title = str(article.get("title") or "").strip() or "未命名文章"
                    try:
                        page = fetch_page(url=url, cfg=cfg)
                    except Exception as exc:
                        pages.append({"url": url, "html": "", "error": repr(exc)})
                        fetch_errors.append(f"[{i}] {keyword}: {url} 备选抓取失败: {exc!r}")
                        continue
                    pages.append(page)
                    html = str(page.get("html") or "")
                    page_error = str(page.get("error") or "").strip()
                    if page_error:
                        fetch_errors.append(f"[{i}] {keyword}: {url} 备选抓取失败: {page_error}")
                        continue
                    if not html:
                        fetch_errors.append(f"[{i}] {keyword}: {url} 备选抓取失败: HTML 为空")
                        continue
                    clipped_html = _clip(html, per_doc_max_chars)
                    candidates.append(
                        {
                            **article,
                            "title": title,
                            "selected": True,
                            "prefetched_html": html,
                        }
                    )
                    extracts.append({"keyword": keyword, "url": url, "title": title, "html": clipped_html})

            if len(extracts) >= target_docs:
                break
    except BaseException as e:
        debug_log(
            f"node=aggregate_article_text exception={type(e).__name__}: {e!r}",
            cfg=cfg,
            prefix="node",
        )
        raise

    if not candidates:
        detail = " | ".join(fetch_errors[:5]) if fetch_errors else "LLM 未筛出可抓取文章"
        out_errors: list[str] = [str(x) for x in (state.get("errors") or []) if str(x).strip()]
        out_errors.append(f"aggregate_article_text 无可用文章候选，已降级跳过文章分析: {detail}")
        out: dict[str, Any] = {
            "article_candidates": [],
            "article_pages": pages,
            "article_extracts": [],
            "article_analysis": "",
            "errors": out_errors,
        }
        if state.get("selected_proposal_id"):
            out["materials_barrier"] = "articles"
        debug_log(
            f"node=aggregate_article_text degraded candidates=0 errors={detail[:200]}",
            cfg=cfg,
            prefix="node",
        )
        return out

    out_errors: list[str] = [str(x) for x in (state.get("errors") or []) if str(x).strip()]
    if len(extracts) < target_docs:
        detail = " | ".join(fetch_errors[:8]) if fetch_errors else "无可用页面"
        out_errors.append(
            f"aggregate_article_text 抓取文章不足 {target_docs} 篇（成功 {len(extracts)}），继续流程: {detail}"
        )

    article_analysis = ""
    if extracts:
        article_analysis = llm_summarize_hot_event_from_html(
            hot_titles=state.get("filtered_keywords") or [],
            article_pages=extracts,
            cfg=cfg,
        ).strip()
        if not article_analysis:
            out_errors.append("aggregate_article_text 未拿到有效的热点详细事件总结（返回空字符串），继续流程")
            article_analysis = ""

    out = {
        "article_candidates": candidates,
        "article_pages": pages,
        "article_extracts": extracts,
        "article_analysis": article_analysis,
    }
    if out_errors:
        out["errors"] = out_errors
    if state.get("selected_proposal_id"):
        out["materials_barrier"] = "articles"
    debug_log(
        f"node=aggregate_article_text done candidates={len(candidates)} extracts={len(extracts)}",
        cfg=cfg,
        prefix="node",
    )
    return out
