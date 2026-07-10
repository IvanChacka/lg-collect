import random
import time
from typing import Any

from langchain_core.runnables import RunnableConfig

from core.state import HotCollectState
from tools.debug_log import debug_log
from tools.search_engines import search_web
from tools.utils import get_config


def _append_excluded_sites(query: str, exclude: str) -> str:
    q = str(query or "").strip()
    raw = str(exclude or "").strip()
    if not q or not raw:
        return q
    # 逗号分隔：zhihu.com,zhuanlan.zhihu.com
    sites = [s.strip() for s in raw.split(",") if s.strip()]
    if not sites:
        return q
    suffix = " ".join([f"-site:{s}" for s in sites])
    merged = f"{q} {suffix}".strip()
    return merged


def search_articles_node(
    state: HotCollectState, config: RunnableConfig | None = None
) -> dict[str, Any]:
    """
    节点：将热点词条/题目在浏览器搜索一次，抓取文章标题 + 链接候选。

    设计目标：
    - 默认只做一次 query（不拆分多个 query）
    - “已选题目”阶段允许最多 N 个 query（由 SELECTED_ARTICLE_QUERIES_LIMIT 控制，且强制上限 3）
    - 每个 query 只取 10 条文章结果
    """

    cfg = get_config(config)
    try:
        import os

        debug_log(
            "node=search_articles context"
            f" cwd={os.getcwd()!r}"
            f" thread_id={str(state.get('thread_id') or cfg.get('thread_id') or '').strip()!r}"
            f" selected_proposal_id={str(state.get('selected_proposal_id') or '').strip()!r}"
            f" status={str(state.get('status') or '').strip()!r}"
            f" keywords_count={len(state.get('filtered_keywords') or [])}",
            cfg=cfg,
            prefix="node",
        )
    except Exception:
        pass
    selected_limit = int(cfg.get("selected_article_queries_limit", 3))
    if selected_limit <= 0:
        raise ValueError(f"SELECTED_ARTICLE_QUERIES_LIMIT 必须为正整数，但得到: {selected_limit!r}")
    # 选题后默认 3 个 query 足够；过大会显著拖慢抓取与聚合，且收益递减。
    selected_limit = min(selected_limit, 3)
    keywords = state.get("filtered_keywords") or []
    per_keyword_limit = int(cfg.get("articles_per_keyword", 10))
    if per_keyword_limit <= 0:
        raise ValueError(f"ARTICLES_PER_KEYWORD 必须为正整数，但得到: {per_keyword_limit!r}")
    # 防止误配导致过慢/过费；更大的候选池收益递减。
    per_keyword_limit = min(per_keyword_limit, 50)

    # 选题阶段：prepare_selected_topic_materials 会生成 3 个更可检索的 query 写入 filtered_keywords。
    # 这里不受默认 keywords_limit=1 的限制，最多跑 selected_limit 个 query（上限 3）。
    if state.get("selected_proposal_id") and len(keywords) > 1:
        effective_keywords = keywords[:selected_limit]
    else:
        # 未选题阶段：filtered_keywords 在 write_hotspot_db 会被设置为 [keyword]（唯一）。
        effective_keywords = keywords[:1]

    results: list[dict] = []
    errors: list[str] = []

    def _compact(value: Any, limit: int) -> str:
        s = str(value or "").replace("\n", " ").replace("\r", " ").strip()
        if len(s) <= limit:
            return s
        return s[: max(0, limit - 1)].rstrip() + "…"

    def _row_brief(row: dict[str, Any]) -> str:
        if not isinstance(row, dict):
            return _compact(row, 120)
        return (
            f"{_compact(row.get('source') or '', 40)} | "
            f"{_compact(row.get('title') or '', 80)} | "
            f"{_compact(row.get('url') or '', 140)}"
        ).strip()

    for kw in effective_keywords:
        query = _build_single_query(state=state, keyword=kw)
        exclude = str(cfg.get("search_exclude_sites") or "")
        query = _append_excluded_sites(query, exclude)
        debug_log(
            "node=search_articles engine='bing_web'"
            f" keyword={kw!r} query={query!r} limit={per_keyword_limit} exclude_sites={exclude!r}",
            cfg=cfg,
            prefix="node",
        )
        try:
            rows = search_web(query=query, cfg=cfg, limit=per_keyword_limit, engine="bing_web")
        except Exception as e:
            errors.append(f"search_articles failed for {kw!r}: {e!r}")
            continue
        debug_log(
            "node=search_articles got_results"
            f" keyword={kw!r} rows={len(rows or [])}"
            + ("" if not rows else f" top1={_row_brief(rows[0])!r}"),
            cfg=cfg,
            prefix="node",
        )
        for i, row in enumerate(rows[:per_keyword_limit], start=1):
            results.append({**row, "keyword": kw, "rank": i})

    # 不允许“静默吞错”：如果部分 query 失败但仍拿到结果，
    # 允许继续推进，但必须把错误写入 state.errors 以便可观测。
    if not results and errors:
        raise RuntimeError(" ; ".join(errors))
    if keywords and not results:
        detail = " | ".join(errors[:3]) if errors else "Bing 未返回任何结果"
        raise RuntimeError(f"search_articles 未返回任何文章候选: {detail}")

    patch: dict[str, Any] = {"article_search_results": results}
    if errors:
        patch["errors"] = errors
    return patch


def search_articles_selected_node(
    state: HotCollectState, config: RunnableConfig | None = None
) -> dict[str, Any]:
    """
    节点（已选题目专用）：用搜狗网页搜索获取文章候选。

    约定：
    - 选题阶段允许最多 N 个 query（由 SELECTED_ARTICLE_QUERIES_LIMIT 控制，且强制上限 3）
    - 每个 query 只取 10 条文章结果（ARTICLES_PER_KEYWORD 可调，但强制上限 50）
    - 不允许静默回退到 Bing；若搜狗未返回结果/命中风控则直接失败并暴露错误
    """

    cfg = get_config(config)
    try:
        import os

        debug_log(
            "node=search_articles_selected context"
            f" cwd={os.getcwd()!r}"
            f" thread_id={str(state.get('thread_id') or cfg.get('thread_id') or '').strip()!r}"
            f" selected_proposal_id={str(state.get('selected_proposal_id') or '').strip()!r}"
            f" status={str(state.get('status') or '').strip()!r}"
            f" keywords_count={len(state.get('filtered_keywords') or [])}",
            cfg=cfg,
            prefix="node",
        )
    except Exception:
        pass

    selected_limit = int(cfg.get("selected_article_queries_limit", 3))
    if selected_limit <= 0:
        raise ValueError(f"SELECTED_ARTICLE_QUERIES_LIMIT 必须为正整数，但得到: {selected_limit!r}")
    selected_limit = min(selected_limit, 3)

    keywords = [str(x).strip() for x in (state.get("filtered_keywords") or []) if str(x).strip()]
    if not state.get("selected_proposal_id"):
        raise RuntimeError("search_articles_selected 仅允许在已选题目后调用（缺少 selected_proposal_id）")
    if not keywords:
        raise RuntimeError("search_articles_selected 缺少 filtered_keywords（应由 prepare_selected_topic_materials 生成）")

    per_keyword_limit = int(cfg.get("articles_per_keyword", 10))
    if per_keyword_limit <= 0:
        raise ValueError(f"ARTICLES_PER_KEYWORD 必须为正整数，但得到: {per_keyword_limit!r}")
    per_keyword_limit = min(per_keyword_limit, 50)

    effective_keywords = keywords[:selected_limit] if len(keywords) > 1 else keywords[:1]

    results: list[dict] = []
    errors: list[str] = []

    def _compact(value: Any, limit: int) -> str:
        s = str(value or "").replace("\n", " ").replace("\r", " ").strip()
        if len(s) <= limit:
            return s
        return s[: max(0, limit - 1)].rstrip() + "…"

    def _row_brief(row: dict[str, Any]) -> str:
        if not isinstance(row, dict):
            return _compact(row, 120)
        return (
            f"{_compact(row.get('source') or '', 40)} | "
            f"{_compact(row.get('title') or '', 80)} | "
            f"{_compact(row.get('url') or '', 140)}"
        ).strip()

    for kw in effective_keywords:
        # 已选题目阶段：filtered_keywords 由 prepare_selected_topic_materials 生成，
        # 本身就是“可检索 query 列表”，不再二次拼接 title/thesis/hot_keyword，避免引入噪音。
        query = str(kw or "").strip()
        exclude = str(cfg.get("search_exclude_sites") or "")
        query = _append_excluded_sites(query, exclude)
        debug_log(
            "node=search_articles_selected engine='sogou_web'"
            f" keyword={kw!r} query={query!r} limit={per_keyword_limit} exclude_sites={exclude!r}",
            cfg=cfg,
            prefix="node",
        )
        # 关键词之间随机延迟 1~3 秒，降低 Sogou 风控概率
        if results:
            time.sleep(random.uniform(1, 3))
        try:
            rows = search_web(
                query=query, cfg=cfg, limit=per_keyword_limit, engine="sogou_web", extract_snippet=False
            )
        except Exception as e:
            errors.append(f"search_articles_selected failed for {kw!r}: {e!r}")
            continue
        debug_log(
            "node=search_articles_selected got_results"
            f" keyword={kw!r} rows={len(rows or [])}"
            + ("" if not rows else f" top1={_row_brief(rows[0])!r}"),
            cfg=cfg,
            prefix="node",
        )
        for i, row in enumerate(rows[:per_keyword_limit], start=1):
            # 已选题目阶段：只保留 title + url，避免在搜索阶段计算/输出 snippet。
            results.append(
                {
                    "title": str((row or {}).get("title") or "").strip(),
                    "url": str((row or {}).get("url") or "").strip(),
                    "keyword": kw,
                    "rank": i,
                    "engine": str((row or {}).get("engine") or "").strip(),
                    "source": str((row or {}).get("source") or "").strip(),
                }
            )

    if not results and errors:
        raise RuntimeError(" ; ".join(errors))
    if keywords and not results:
        detail = " | ".join(errors[:3]) if errors else "Sogou 未返回任何结果"
        raise RuntimeError(f"search_articles_selected 未返回任何文章候选: {detail}")

    patch: dict[str, Any] = {"article_search_results": results}
    if errors:
        patch["errors"] = errors
    return patch


def _find_selected_proposal(state: HotCollectState) -> dict[str, Any] | None:
    proposal_id = str(state.get("selected_proposal_id") or "").strip()
    if not proposal_id:
        return None
    for proposal in state.get("proposals") or []:
        if str(proposal.get("proposal_id") or "").strip() == proposal_id:
            return proposal
    return None


def _build_single_query(*, state: HotCollectState, keyword: str) -> str:
    """
    只构造一个 query：
    - 若 filtered_keywords 已经是“显式 query 列表”（例如选题阶段的 3 个 query），直接使用 keyword。
    - 否则优先使用“题目/选题”本身，其次使用热点词条。
    """

    if state.get("selected_proposal_id") and len(state.get("filtered_keywords") or []) > 1:
        return str(keyword or "").strip()

    if state.get("selected_proposal_id"):
        proposal = _find_selected_proposal(state)
        title = str((proposal or {}).get("title") or "").strip()
        thesis = str((proposal or {}).get("thesis") or "").strip()
        hot_keyword = str(state.get("selected_hot_keyword") or "").strip()

        merged = " ".join([x for x in [title, thesis, hot_keyword] if x]).strip()
        if merged:
            return merged

    return str(keyword or "").strip()


def _augment_query(query: str) -> str:
    """
    保留该函数仅用于兼容历史调用点（当前不再改写 query）。
    搜索结果的筛选交给下游 LLM 处理，避免“看起来像换了搜索源”的误判。
    """

    return str(query or "").strip()
