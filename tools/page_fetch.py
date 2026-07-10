from __future__ import annotations

import re
from typing import Any
from urllib.parse import urljoin, urlparse

from tools.http import get_text


def _detect_blocked_html(html: str) -> str | None:
    text = str(html or "").strip()
    if not text:
        return "empty_html"
    lowered = text.lower()
    blocked_signals = (
        "您当前请求存在异常，暂时限制本次访问",
        "暂时限制本次访问",
        "40362",
        "403 forbidden",
        "429 too many requests",
        "451 unavailable",
        "access denied",
        "captcha",
    )
    if any(signal.lower() in lowered for signal in blocked_signals):
        return "page_blocked"
    return None


def _extract_js_or_meta_redirect_url(*, html: str, base_url: str) -> str | None:
    """
    尝试从“跳转页”HTML 中提取真实目标 URL（如 sogou.com/link）。

    典型形式：
    - window.location.replace("https://...")
    - window.location.href="https://..."
    - window.location="https://..."
    - <meta http-equiv="refresh" content="0;URL='https://...'">
    """

    raw = str(html or "")
    if not raw or len(raw) > 200_000:
        # 过大的页面不做此类检测，避免正则开销
        return None

    patterns = [
        r"window\.location\.(?:replace|href)\(\s*['\"]([^'\"]+)['\"]\s*\)",
        r"window\.location\s*=\s*['\"]([^'\"]+)['\"]",
    ]
    for p in patterns:
        m = re.search(p, raw, flags=re.I)
        if m and m.group(1):
            target = m.group(1).strip()
            if target:
                return urljoin(base_url, target)

    # meta refresh（尽量兼容大小写/单引号/双引号）
    m2 = re.search(
        r"<meta[^>]+http-equiv\s*=\s*['\"]?refresh['\"]?[^>]+content\s*=\s*['\"]([^'\"]+)['\"]",
        raw,
        flags=re.I,
    )
    if m2 and m2.group(1):
        content = m2.group(1)
        m3 = re.search(r"url\s*=\s*('?)([^;\n\r'\"<> ]+)\1", content, flags=re.I)
        if m3 and m3.group(2):
            target = m3.group(2).strip()
            if target:
                return urljoin(base_url, target)

    return None


def _is_likely_redirect_stub(html: str) -> bool:
    t = str(html or "").strip().lower()
    if not t:
        return False
    # 常见“无意义跳转页”：只有 meta refresh / window.location
    if len(t) < 2000 and ("window.location" in t or "http-equiv" in t and "refresh" in t):
        return True
    return False


def _domain_allowed(*, url: str, cfg: dict[str, Any]) -> str | None:
    """
    对 URL 做域名 allow/block 校验；返回 error string 或 None。
    """

    allowed = (cfg.get("crawl_allowed_domains") or "").strip()
    if allowed:
        host = (urlparse(url).hostname or "").lower()
        ok = any(host == d or host.endswith("." + d) for d in allowed.split(",") if d.strip())
        if not ok:
            return "domain_not_allowed"

    blocked = (cfg.get("crawl_blocked_domains") or "").strip()
    if blocked:
        host = (urlparse(url).hostname or "").lower()
        if host and any(host == d or host.endswith("." + d) for d in blocked.split(",") if d.strip()):
            return "domain_blocked"
    return None


def fetch_page(*, url: str, cfg: dict[str, Any]) -> dict:
    """
    抓取网页 HTML。这里先做最小实现，后续可加入代理/重试/缓存/反爬策略。
    """

    domain_err = _domain_allowed(url=url, cfg=cfg)
    if domain_err:
        return {"url": url, "html": "", "error": domain_err}

    try:
        html = get_text(
            url=url,
            cache_ttl_seconds=int(cfg.get("page_cache_ttl_seconds") or 3600),
            timeout=20,
        )
        # 某些“跳转页”（如 sogou.com/link）返回的是 JS/meta refresh，
        # 直接聚合会得到无意义内容；这里解析真实目标 URL 并再次抓取。
        if _is_likely_redirect_stub(html):
            target = _extract_js_or_meta_redirect_url(html=html, base_url=url)
            if target and target != url:
                target_domain_err = _domain_allowed(url=target, cfg=cfg)
                if target_domain_err:
                    return {"url": url, "html": "", "error": target_domain_err, "final_url": target}
                html2 = get_text(
                    url=target,
                    cache_ttl_seconds=int(cfg.get("page_cache_ttl_seconds") or 3600),
                    timeout=20,
                )
                reason2 = _detect_blocked_html(str(html2 or ""))
                if reason2:
                    return {"url": url, "html": "", "error": reason2, "final_url": target}
                return {"url": url, "html": str(html2 or ""), "final_url": target}

        reason = _detect_blocked_html(str(html or ""))
        if reason:
            return {"url": url, "html": "", "error": reason}
        return {"url": url, "html": str(html or "")}
    except Exception as exc:
        # 反爬常见：403/429 等，尝试用 Playwright 抓取同一个 URL（不切换来源，不做兜底站点）。
        msg = str(exc)
        if any(token in msg for token in ("403 Forbidden", "429 Too Many Requests", "451 Unavailable")):
            try:
                html = _fetch_page_html_playwright(url=url)
                if _is_likely_redirect_stub(html):
                    target = _extract_js_or_meta_redirect_url(html=html, base_url=url)
                    if target and target != url:
                        target_domain_err = _domain_allowed(url=target, cfg=cfg)
                        if target_domain_err:
                            return {"url": url, "html": "", "error": target_domain_err, "final_url": target}
                        html2 = _fetch_page_html_playwright(url=target)
                        reason2 = _detect_blocked_html(html2)
                        if reason2:
                            return {"url": url, "html": "", "error": reason2, "final_url": target}
                        return {"url": url, "html": str(html2 or ""), "final_url": target}
                reason = _detect_blocked_html(html)
                if reason:
                    return {"url": url, "html": "", "error": reason}
                return {"url": url, "html": str(html or "")}
            except Exception as exc2:
                raise RuntimeError(f"GET 失败(HTTP) 且 Playwright 抓取失败: {url!r}: {exc2!r}") from exc2
        raise


def _fetch_page_html_playwright(*, url: str) -> str:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 2200})
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            try:
                page.wait_for_load_state("networkidle", timeout=15000)
            except Exception:
                pass
            html = page.content()
        finally:
            browser.close()

    if not html or len(html.strip()) < 200:
        raise RuntimeError("Playwright 返回 HTML 为空/过短")
    return html
