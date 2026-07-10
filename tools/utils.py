from __future__ import annotations

import os
import uuid
from collections.abc import Mapping
from datetime import date
from pathlib import Path
from typing import Any

from dotenv import dotenv_values


def new_thread_id(*, prefix: str = "hot_topic", run_date: str | None = None) -> str:
    d = run_date or date.today().isoformat()
    short = uuid.uuid4().hex[:8]
    return f"{prefix}_{d}_{short}"


def get_config(config: Any | None) -> dict[str, Any]:
    # 优先读取本仓库根目录的 .env（以当前文件位置为锚点），避免：
    # - LangGraph dev / Studio 的工作目录（CWD）不是 repo root，导致读取到别的 `.env` 或读不到；
    # - 长驻进程 / IDE / Studio 中的旧环境变量把本仓库配置“压回去”。
    #
    # 优先级：LangGraph configurable > repo_root/.env > 进程环境变量。
    repo_env_path = Path(__file__).resolve().parents[1] / ".env"
    env_file = dotenv_values(str(repo_env_path)) if repo_env_path.exists() else dotenv_values(".env")

    def _configurable_from_config(c: Any | None) -> dict[str, Any]:
        """
        LangGraph/LangChain 运行时传进来的 config 可能是：
        - 原生 dict
        - Mapping
        - 具有 .get(...) 的类 dict 对象（不一定注册为 Mapping）
        这里统一提取出 configurable dict，尽量保留原始类型（bool/int 等）。
        """

        if c is None:
            return {}
        if isinstance(c, dict):
            v = c.get("configurable", {})
            return v if isinstance(v, dict) else {}
        if isinstance(c, Mapping):
            try:
                v = c.get("configurable", {})
                if isinstance(v, dict):
                    return v
            except Exception:
                pass
            try:
                d = dict(c)
                v = d.get("configurable", {})
                return v if isinstance(v, dict) else {}
            except Exception:
                return {}
        if hasattr(c, "get"):
            try:
                v = c.get("configurable", {})
                return v if isinstance(v, dict) else {}
            except Exception:
                return {}
        return {}

    configurable_cfg = _configurable_from_config(config)

    def pick(name: str, default: str = "") -> str:
        v_cfg = None
        if configurable_cfg:
            v_cfg = configurable_cfg.get(name) or configurable_cfg.get(name.lower())
        def _clean(raw: Any) -> str:
            s = str(raw)
            # python-dotenv 的 dotenv_values 对“KEY=value  # comment”会把注释也当成 value；
            # 本项目 .env 中大量使用该写法，因此这里统一做一次“行内注释剥离”。
            #
            # 规则：
            # - 仅剥离以空格分隔的注释：`" #"`
            # - 若剥离后只剩空白或以 `#` 开头，则视为未配置
            if " #" in s:
                s = s.split(" #", 1)[0]
            s = s.strip()
            if not s or s.startswith("#"):
                return ""
            return s

        if v_cfg is not None and str(v_cfg) != "":
            cleaned = _clean(v_cfg)
            return cleaned if cleaned != "" else default
        v2 = env_file.get(name)
        if v2 is not None and str(v2) != "":
            cleaned = _clean(v2)
            return cleaned if cleaned != "" else default
        v = os.getenv(name)
        if v is not None and str(v) != "":
            cleaned = _clean(v)
            return cleaned if cleaned != "" else default
        return default

    cfg = configurable_cfg
    env = {
        "llm_provider": pick("LLM_PROVIDER", "minimax"),
        # 0 表示不限制（尽量使用模型/服务端允许的最大输出长度）
        "llm_max_tokens": int(pick("LLM_MAX_TOKENS", "0")),
        "openai_api_key": pick("OPENAI_API_KEY", ""),
        "openai_base_url": pick("OPENAI_BASE_URL", "https://api.openai.com/v1"),
        "openai_model": pick("OPENAI_MODEL", "gpt-4.1-mini"),
        "openai_temperature": float(pick("OPENAI_TEMPERATURE", "0.4")),
        # DeepSeek (OpenAI-compatible)
        "deepseek_api_key": pick("DEEPSEEK_API_KEY", ""),
        "deepseek_base_url": pick("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        "deepseek_model": pick("DEEPSEEK_MODEL", "deepseek-v4-pro"),
        "deepseek_temperature": float(pick("DEEPSEEK_TEMPERATURE", "0.2")),
        "deepseek_timeout_seconds": float(pick("DEEPSEEK_TIMEOUT_SECONDS", "300")),
        "minimax_api_key": pick("MINIMAX_API_KEY", ""),
        "minimax_base_url": pick("MINIMAX_BASE_URL", "https://api.minimaxi.com/v1"),
        "minimax_model": pick("MINIMAX_MODEL", "minimax/MiniMax-M2.7-HighSpeed"),
        "minimax_temperature": float(pick("MINIMAX_TEMPERATURE", "0.4")),
        "minimax_timeout_seconds": float(pick("MINIMAX_TIMEOUT_SECONDS", "300")),
        "minimax_reasoning_split": pick("MINIMAX_REASONING_SPLIT", "0"),
        "llm_debug": pick("LLM_DEBUG", "0"),
        "llm_debug_log_path": pick("LLM_DEBUG_LOG_PATH", ".data/llm_debug.log"),
        "hot_keyword_llm_input_limit": int(pick("HOT_KEYWORD_LLM_INPUT_LIMIT", "50")),
        "hot_keyword_llm_output_limit": int(pick("HOT_KEYWORD_LLM_OUTPUT_LIMIT", "30")),
        "hot_db_path": pick("HOT_DB_PATH", ".data/hot_history.sqlite"),
        "hot_candidate_per_platform_limit": int(pick("HOT_CANDIDATE_PER_PLATFORM_LIMIT", "10")),
        "hot_source": pick("HOT_SOURCE", "official"),
        # 允许分别指定微博/抖音的数据源（优先级高于 hot_source）
        "weibo_hot_source": pick("WEIBO_HOT_SOURCE", ""),
        "douyin_hot_source": pick("DOUYIN_HOT_SOURCE", ""),
        "search_provider": pick("SEARCH_PROVIDER", ""),
        # 搜索 query 级别的站点排除（Bing 支持 -site:xxx）
        # 例：SEARCH_EXCLUDE_SITES=zhihu.com,zhuanlan.zhihu.com
        "search_exclude_sites": pick("SEARCH_EXCLUDE_SITES", ""),
        # 搜索/爬虫稳定性（可选）
        "crawl_proxy": pick("CRAWL_PROXY", ""),
        # 搜索抓取模式（http/playwright）
        "search_fetch_mode": pick("SEARCH_FETCH_MODE", "http"),
        # Sogou 搜索抓取模式（独立于 search_fetch_mode，默认跟随 search_fetch_mode）
        "sogou_fetch_mode": pick("SOGOU_FETCH_MODE", ""),
        # 搜索请求超时（秒）
        "search_timeout_seconds": float(pick("SEARCH_TIMEOUT_SECONDS", "30")),
        # 显式阻止抓取的域名黑名单（逗号分隔；空表示不限制）
        "crawl_blocked_domains": pick("CRAWL_BLOCKED_DOMAINS", ""),
        "search_max_attempts": int(pick("SEARCH_MAX_ATTEMPTS", "3")),
        "search_retry_backoff_seconds": float(pick("SEARCH_RETRY_BACKOFF_SECONDS", "1.5")),
        "search_debug_dump": pick("SEARCH_DEBUG_DUMP", "0"),
        "search_debug_dump_mode": pick("SEARCH_DEBUG_DUMP_MODE", "on_failure"),
        "search_debug_dump_screenshot": pick("SEARCH_DEBUG_DUMP_SCREENSHOT", "0"),
        "search_debug_dir": pick("SEARCH_DEBUG_DIR", ".data/search_debug"),
        "weibo_hot_url": pick("WEIBO_HOT_URL", ""),
        "douyin_hot_url": pick("DOUYIN_HOT_URL", ""),
        "douyin_hot_api_url": pick("DOUYIN_HOT_API_URL", ""),
        # 默认每个 query 只抓取 10 条文章候选：
        # - 下游还会做 LLM 筛选与正文聚合，候选池过大会显著拖慢链路
        # - 需要更多候选时可显式设置 ARTICLES_PER_KEYWORD（节点仍会做上限保护）
        "articles_per_keyword": int(pick("ARTICLES_PER_KEYWORD", "10")),
        # 已选题阶段：素材搜索 query 上限（默认 3；建议 <=3）
        "selected_article_queries_limit": int(pick("SELECTED_ARTICLE_QUERIES_LIMIT", "3")),
        "article_llm_keep_limit": int(pick("ARTICLE_LLM_KEEP_LIMIT", "20")),
        "article_llm_input_limit": int(pick("ARTICLE_LLM_INPUT_LIMIT", "50")),
        "article_aggregate_max_chars": int(pick("ARTICLE_AGGREGATE_MAX_CHARS", "40000")),
        "article_doc_max_chars": int(pick("ARTICLE_DOC_MAX_CHARS", "6000")),
        # 默认抓取 30 条即可：后续还会走 LLM 筛选 + 下载/转写，100 条会显著拖慢全链路。
        "videos_per_keyword": int(pick("VIDEOS_PER_KEYWORD", "30")),
        # 已选题阶段：视频素材搜索 query 上限（默认 3；建议 <=3）
        "selected_video_queries_limit": int(pick("SELECTED_VIDEO_QUERIES_LIMIT", "3")),
        "video_provider": pick("VIDEO_PROVIDER", ""),
        # 每个 query 默认只保留 3 条：由 LLM 控制数量，下游节点不再做截断。
        "video_llm_keep_limit": int(pick("VIDEO_LLM_KEEP_LIMIT", "3")),
        "video_llm_input_limit": int(pick("VIDEO_LLM_INPUT_LIMIT", "60")),
        # 兼容旧配置：不建议再用下游节点做截断（应由 LLM keep_limit 控制）
        "video_download_limit": int(pick("VIDEO_DOWNLOAD_LIMIT", "0")),
        # 下载阶段失败策略：
        # - partial: 允许部分下载失败，但会写入 state.errors（默认，避免单个失效链接导致全链路中断）
        # - raise: 任一失败即抛异常（旧行为）
        "video_download_fail_policy": pick("VIDEO_DOWNLOAD_FAIL_POLICY", "partial"),
        "video_download_concurrency": int(pick("VIDEO_DOWNLOAD_CONCURRENCY", "3")),
        # 转写阶段失败策略：
        # - partial: 允许部分转写失败（会在 video_assets 里标注 subtitle_generated=False + reason）
        # - raise: 任一失败即抛异常（严格模式）
        "video_transcribe_fail_policy": pick("VIDEO_TRANSCRIBE_FAIL_POLICY", "partial"),
        "video_transcribe_prefer_subtitle": pick("VIDEO_TRANSCRIBE_PREFER_SUBTITLE", "1"),
        "video_subtitle_min_chars": int(pick("VIDEO_SUBTITLE_MIN_CHARS", "80")),
        "bbdown_bin": pick("BBDOWN_BIN", "BBDown"),
        # BBDown 的 --skip-ai 默认开启（即默认跳过 AI 字幕）。
        # 本工作流希望优先拿到“可用字幕”，因此默认不跳过（BBDOWN_SKIP_AI=0）。
        "bbdown_skip_ai": pick("BBDOWN_SKIP_AI", "0"),
        "bilibili_cookie": pick("BILIBILI_COOKIE", ""),
        "iflytek_appid": pick("IFLYTEK_APPID", ""),
        "iflytek_api_key": pick("IFLYTEK_API_KEY", ""),
        "iflytek_api_secret": pick("IFLYTEK_API_SECRET", ""),
        # 旧 WebSocket 实时听写相关配置已废弃（本项目改为“极速语音转写”HTTP任务式）。
        # 极速语音转写（HTTP 任务式）
        "iflytek_speed_poll_interval_seconds": float(pick("IFLYTEK_SPEED_POLL_INTERVAL_SECONDS", "2")),
        "iflytek_speed_deadline_seconds": float(pick("IFLYTEK_SPEED_DEADLINE_SECONDS", "900")),
        "iflytek_speed_upload_timeout_seconds": float(pick("IFLYTEK_SPEED_UPLOAD_TIMEOUT_SECONDS", "120")),
        "iflytek_speed_create_timeout_seconds": float(pick("IFLYTEK_SPEED_CREATE_TIMEOUT_SECONDS", "60")),
        "iflytek_speed_query_timeout_seconds": float(pick("IFLYTEK_SPEED_QUERY_TIMEOUT_SECONDS", "60")),
        "iflytek_speed_language": pick("IFLYTEK_SPEED_LANGUAGE", "zh_cn"),
        "iflytek_speed_domain": pick("IFLYTEK_SPEED_DOMAIN", "pro_ost_ed"),
        "iflytek_speed_accent": pick("IFLYTEK_SPEED_ACCENT", "mandarin"),
        "iflytek_speed_tmp_dir": pick("IFLYTEK_SPEED_TMP_DIR", ".data/asr_speed_tmp"),
        "iflytek_speed_concurrency": int(pick("IFLYTEK_SPEED_CONCURRENCY", "2")),
        "hot_db_disable_dedup": pick("HOT_DB_DISABLE_DEDUP", "0"),
        "crawl_allowed_domains": pick("CRAWL_ALLOWED_DOMAINS", ""),
        "page_cache_ttl_seconds": int(pick("PAGE_CACHE_TTL_SECONDS", "3600")),
        "feishu_webhook_url": pick("FEISHU_WEBHOOK_URL", ""),
        "feishu_api_base": pick("FEISHU_API_BASE", "https://open.feishu.cn"),
        "feishu_app_id": pick("FEISHU_APP_ID", ""),
        "feishu_app_secret": pick("FEISHU_APP_SECRET", ""),
        "feishu_chat_id": pick("FEISHU_CHAT_ID", ""),
        "feishu_ws_enable": pick("FEISHU_WS_ENABLE", "1"),
        "feishu_selection_timeout_seconds": int(pick("FEISHU_SELECTION_TIMEOUT_SECONDS", "60")),
    }
    merged = {**env, **cfg}
    def _as_bool(v: Any, *, default: bool) -> bool:
        if v is None:
            return default
        if isinstance(v, bool):
            return v
        s = str(v).strip().lower()
        if s in ("1", "true", "yes", "y", "on"):
            return True
        if s in ("0", "false", "no", "n", "off", ""):
            return False
        return default

    merged["human_mode"] = _as_bool(merged.get("human_mode"), default=True)

    # 移除 mock：即使上游（Studio/旧配置）传了 mock，也强制回落到 deepseek
    provider = (merged.get("llm_provider") or "").lower().strip()
    if provider == "mock":
        merged["llm_provider"] = "deepseek"
    return merged
