from __future__ import annotations

from typing import Any

from langchain_core.runnables import RunnableConfig

from core.state import HotCollectState
from tools.debug_log import debug_log
from tools.page_fetch import fetch_page
from tools.utils import get_config


def _html_to_main_text(html: str) -> str:
    """
    将网页 HTML 清洗为“尽量接近正文”的纯文本（无标签）。
    目标：避免把 header/nav/script/style 等噪音拼进下游材料。
    """

    raw = str(html or "").strip()
    if not raw:
        return ""

    try:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(raw, "lxml")

        # 删除明显非正文节点
        for tag in soup(["script", "style", "noscript", "iframe", "svg"]):
            tag.decompose()
        for tag in soup.find_all(["header", "footer", "nav", "aside"]):
            tag.decompose()

        # 优先取 <article>，否则取 <main>，否则回落到 body
        container = soup.find("article") or soup.find("main") or soup.body or soup

        # 去掉常见噪音容器
        for selector in (
            ".comment",
            ".comments",
            ".related",
            ".recommend",
            ".recommends",
            ".breadcrumb",
            ".share",
            ".toolbar",
            ".advert",
            ".ads",
            ".ad",
        ):
            for t in container.select(selector):
                t.decompose()

        text = container.get_text("\n", strip=True)
        # 合并多余空行
        lines = [ln.strip() for ln in text.splitlines()]
        lines = [ln for ln in lines if ln]
        cleaned = "\n".join(lines).strip()

        # 站点尾巴/导流文案清理（保守处理，避免误删正文）
        tail_patterns = (
            "返回搜狐，查看更多",
            "点击查看更多",
            "责任编辑",
            "举报/反馈",
        )
        for p in tail_patterns:
            if p in cleaned:
                cleaned = cleaned.split(p, 1)[0].rstrip()

        return cleaned
    except Exception:
        # 解析失败时回落为原始字符串（下游仍会做截断）
        return raw


def fetch_article_contents_selected_node(
    state: HotCollectState, config: RunnableConfig | None = None
) -> dict[str, Any]:
    """
    节点：对 llm_filter_articles_selected 选出的文章 URL 进行抓取，写入 article_extracts，
    供 aggregate_keyword_materials_selected 使用。

    约束：
    - 不做“静默兜底/切源”；抓取失败会写入 state.errors，且会在无法抓取任何文章时直接报错。
    - 不在此节点做摘要/分析，只负责拿到“可用正文”（纯文本，可截断）。
    """

    cfg = get_config(config)
    per_doc_max_chars = int(cfg.get("selected_article_doc_max_chars") or 12000)
    total_max_docs = int(cfg.get("selected_article_fetch_limit") or 8)
    if per_doc_max_chars <= 0:
        raise ValueError(f"SELECTED_ARTICLE_DOC_MAX_CHARS 必须为正整数，但得到: {per_doc_max_chars!r}")
    if total_max_docs <= 0:
        raise ValueError(f"SELECTED_ARTICLE_FETCH_LIMIT 必须为正整数，但得到: {total_max_docs!r}")

    candidates = [x for x in (state.get("article_candidates") or []) if isinstance(x, dict)]
    if not state.get("selected_proposal_id"):
        raise RuntimeError("fetch_article_contents_selected 仅允许在已选题目后调用（缺少 selected_proposal_id）")
    if not candidates:
        raise RuntimeError("fetch_article_contents_selected 缺少 article_candidates（应由 llm_filter_articles_selected 生成）")

    debug_log(
        f"node=fetch_article_contents_selected start candidates={len(candidates)} limit={total_max_docs}",
        cfg=cfg,
        prefix="node",
    )

    def _clip(text: str, max_chars: int) -> str:
        if max_chars <= 0:
            return text
        if len(text) <= max_chars:
            return text
        return text[:max_chars] + "\n\n...(truncated)...\n"

    extracts: list[dict[str, Any]] = []
    errors: list[str] = [str(x) for x in (state.get("errors") or []) if str(x).strip()]
    seen_urls: set[str] = set()

    for cand in candidates:
        if len(extracts) >= total_max_docs:
            break
        url = str(cand.get("url") or "").strip()
        if not url:
            errors.append(f"fetch_article_contents_selected: 缺少文章 URL: {cand!r}")
            continue
        if url in seen_urls:
            continue
        seen_urls.add(url)

        keyword = str(cand.get("keyword") or "").strip()
        title = str(cand.get("title") or "").strip() or "未命名文章"
        try:
            page = fetch_page(url=url, cfg=cfg)
        except Exception as e:
            errors.append(f"fetch_article_contents_selected: {url} 抓取失败: {e!r}")
            continue

        html = str(page.get("html") or "")
        page_error = str(page.get("error") or "").strip()
        if page_error:
            errors.append(f"fetch_article_contents_selected: {url} 抓取失败: {page_error}")
            continue
        if not html:
            errors.append(f"fetch_article_contents_selected: {url} 抓取失败: HTML 为空")
            continue

        text = _html_to_main_text(html)
        if not text:
            errors.append(f"fetch_article_contents_selected: {url} 抓取失败: 正文为空/无法提取")
            continue

        extracts.append({"keyword": keyword, "url": url, "title": title, "text": _clip(text, per_doc_max_chars)})

    if not extracts:
        detail = " | ".join(errors[-6:]) if errors else "全部抓取失败"
        raise RuntimeError(f"fetch_article_contents_selected 无可用文章正文: {detail}")

    debug_log(
        f"node=fetch_article_contents_selected done extracts={len(extracts)}",
        cfg=cfg,
        prefix="node",
    )

    out: dict[str, Any] = {"article_extracts": extracts}
    if errors:
        out["errors"] = errors
    return out
