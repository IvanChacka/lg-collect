from __future__ import annotations

import json
import re
import time
from typing import Any
from urllib.parse import urlparse

import httpx

from core.state import DeepInsight, Material, TopicProposal
from tools.debug_log import debug_log
from tools.llm_proposals import llm_infer_account_topics_and_generate_proposals as _llm_infer_and_generate


def _parse_truthy(value: Any) -> bool:
    """
    把常见的 env/config 值解析为 bool，避免 `"0"` / `"false"` 这类字符串在 Python 中被当成 True。
    """

    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, (int, float)):
        return bool(int(value))
    s = str(value).strip().lower()
    if s in ("1", "true", "yes", "y", "on"):
        return True
    if s in ("0", "false", "no", "n", "off", ""):
        return False
    try:
        return bool(int(s))
    except Exception:
        return bool(s)


def _normalize_minimax_model(model: str) -> str:
    """
    兼容不同的 MiniMax 模型命名：
    - 有些控制台/示例会写成 `minimax/MiniMax-M2.7-HighSpeed`
    - API 文档中常见为 `MiniMax-M2.7-highspeed`
    """

    m = (model or "").strip()
    if not m:
        return ""
    if "/" in m:
        m = m.split("/", 1)[1].strip()
    # 统一 HighSpeed / highspeed
    m = m.replace("HighSpeed", "highspeed").replace("HIGH_SPEED", "highspeed")
    # 统一大小写（保留 MiniMax 前缀大小写，后缀小写即可）
    if m.lower().endswith("-highspeed") and not m.endswith("-highspeed"):
        m = m[: -len("-highspeed")] + "-highspeed"
    return m


def _llm_chat(messages: list[dict[str, Any]], *, cfg: dict[str, Any]) -> str:
    provider = (cfg.get("llm_provider") or "").lower().strip()
    if not provider:
        raise RuntimeError("未设置 LLM_PROVIDER：请在 .env 填写 LLM_PROVIDER=deepseek")

    # 全局超时重试（仅对 TimeoutException 生效，避免偶发网络抖动导致整条工作流停摆）。
    # 注意：这是"真实请求失败后的重试"，不是 mock/静默兜底；超过次数仍会抛错终止流程。
    max_attempts = 5
    base_backoff_seconds = 2.0
    last_err: BaseException | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            if provider in ("openai_compat", "openai-compatible", "openai", "deepseek"):
                return _openai_compat_chat(messages, cfg=cfg)
            if provider == "minimax":
                return _minimax_chat(messages, cfg=cfg)
            raise RuntimeError(f"不支持的 LLM_PROVIDER: {provider}")
        except httpx.TimeoutException as e:
            last_err = e
            debug_log(
                f"llm timeout provider={provider!r} attempt={attempt}/{max_attempts} "
                f"exc={type(e).__name__}: {e!r}",
                cfg=cfg,
            )
            if attempt >= max_attempts:
                break
            # 指数退避：2s, 4s, 8s, 16s（第 1 次失败后 sleep=2s）
            sleep_s = base_backoff_seconds * (2 ** (attempt - 1))
            time.sleep(sleep_s)

    assert last_err is not None
    raise last_err


def _strip_think_blocks(text: str) -> str:
    if not text:
        return text
    # 常见格式：`<think>... </think>`（有闭合标签）
    if re.search(r"</think>", text or "", flags=re.IGNORECASE):
        cleaned = re.sub(r"<think>[\s\S]*?</think>\s*", "", text, flags=re.IGNORECASE).strip()
        if cleaned:
            return cleaned
    t = text.strip()
    if t.lower().startswith("<think>"):
        # 兼容模型输出被截断导致缺失 `</think>` 的情况：
        # 优先尝试从第一个"看起来像正文的段落"开始截取，避免把思维链泄漏到最终输出。
        # 口播文案场景：正文应以中文开头（更稳健地从这里截断）。
        m = re.search(r"^<think>[\s\S]*?\n\s*\n(?=[\u4e00-\u9fff])", t, flags=re.IGNORECASE)
        if m:
            tail = t[m.end() :].strip()
            if tail:
                # 防止残留闭合标签
                tail = re.sub(r"</think>\s*$", "", tail, flags=re.IGNORECASE).strip()
                return tail

        # JSON/数组输出的兜底：从第一个 `{` / `[` 之后开始截取（用于结构化输出场景）。
        idxs = [i for i in (t.find("{"), t.find("[")) if i != -1]
        if idxs:
            tail = t[min(idxs) :].strip()
            if tail:
                return tail
    return t


def _debug_log_json(name: str, data: Any, *, cfg: dict[str, Any]) -> None:
    try:
        text = json.dumps(data, ensure_ascii=False)
    except Exception:
        text = repr(data)
    debug_log(f"{name}={text}", cfg=cfg)


def _compact_llm_text(value: Any, *, limit: int) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def _result_source(url: Any) -> str:
    raw = str(url or "").strip()
    if not raw:
        return ""
    try:
        host = urlparse(raw).netloc.strip().lower()
    except Exception:
        return ""
    if host.startswith("www."):
        host = host[4:]
    return host


def _normalized_rank_title(value: Any) -> str:
    text = re.sub(r"\s+", "", str(value or "")).lower()
    return re.sub(r"[|_·•,，:：!！?？#（）()\\-]+", "", text)


def _keyword_token_hits(keyword: str, text: str) -> int:
    hay = re.sub(r"\s+", "", str(text or "")).lower()
    if not hay:
        return 0
    hits = 0
    for token in re.findall(r"[a-zA-Z0-9.+-]+|[\u4e00-\u9fff]{2,}", str(keyword or "")):
        needle = re.sub(r"\s+", "", token).lower()
        if needle and needle in hay:
            hits += 1
    return hits


def _score_article_candidate(keyword: str, article: dict[str, Any]) -> int:
    title = str(article.get("title") or "")
    text = title
    score = _keyword_token_hits(keyword, text) * 3

    good_terms = ("技术报告", "开源", "正式发布", "预览版本", "多模态", "昇腾", "华为")
    bad_terms = (
        "震撼",
        "爆料",
        "泄露",
        "真要来了",
        "来自",
        "什么看法",
        "小米",
        "etf",
        "知乎",
        "百科",
        "重大变化",
        "深夜突发",
    )
    for term in good_terms:
        if term.lower() in text.lower():
            score += 2
    for term in bad_terms:
        if term.lower() in text.lower():
            score -= 3
    return score


def _prefilter_article_candidates(keyword: str, articles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ranked: list[tuple[int, int, dict[str, Any]]] = []
    for idx, article in enumerate(articles):
        if not isinstance(article, dict):
            continue
        ranked.append((_score_article_candidate(keyword, article), idx, article))

    ranked.sort(key=lambda x: (-x[0], x[1]))
    out: list[dict[str, Any]] = []
    seen_titles: set[str] = set()
    for _, _, article in ranked:
        title_key = _normalized_rank_title(article.get("title"))
        if title_key and title_key in seen_titles:
            continue
        if title_key:
            seen_titles.add(title_key)
        out.append(article)
        if len(out) >= 12:
            break
    return out


def _extract_json_array(text: str) -> list[Any] | None:
    """
    从模型输出里尽量提取 JSON 数组。
    兼容常见的不合规输出（例如包了 ```json 或前后夹杂解释/思考）。
    """

    if not text:
        return None
    t = text.strip()
    # 去掉常见 code fence
    if t.startswith("```"):
        t = t.strip("`").strip()
        if t.lower().startswith("json"):
            t = t[4:].strip()

    # 优先：整个就是数组
    try:
        data = json.loads(t)
        if isinstance(data, list):
            return data
    except Exception:
        pass

    # 退一步：找第一个 '[' 到最后一个 ']'
    l = t.find("[")
    r = t.rfind("]")
    if l != -1 and r != -1 and r > l:
        cand = t[l : r + 1]
        try:
            data = json.loads(cand)
            if isinstance(data, list):
                return data
        except Exception:
            pass

    # 再退一步：找最短的 [...] 片段逐个尝试
    import re

    for m in re.finditer(r"\[[\s\S]*?\]", t):
        cand = m.group(0)
        try:
            data = json.loads(cand)
            if isinstance(data, list):
                return data
        except Exception:
            continue
    return None


def _extract_json_object(text: str) -> dict[str, Any] | None:
    """
    从模型输出里尽量提取 JSON 对象。
    兼容：前后夹杂解释、<think>、代码块等。
    """

    if not text:
        return None
    t = text.strip()
    # 去掉常见 code fence
    if t.startswith("```"):
        t = t.strip("`").strip()
        if t.lower().startswith("json"):
            t = t[4:].strip()

    # 常见 think tag
    t = t.replace("<think>", "").replace("</think>", "").strip()

    try:
        data = json.loads(t)
        if isinstance(data, dict):
            return data
    except Exception:
        pass

    # 尝试：从每个可能的 '{' 起点做 raw_decode，支持正文里夹带一个完整嵌套 JSON 对象。
    decoder = json.JSONDecoder()
    found: dict[str, Any] | None = None
    start = 0
    while True:
        l = t.find("{", start)
        if l == -1:
            break
        try:
            data, _ = decoder.raw_decode(t[l:])
            if isinstance(data, dict):
                found = data
        except Exception:
            pass
        start = l + 1
    if found is not None:
        return found

    # 退一步：用 first '{' 到 last '}'（可能会失败，但比没有强）
    l = t.find("{")
    r = t.rfind("}")
    if l != -1 and r != -1 and r > l:
        cand = t[l : r + 1]
        try:
            data = json.loads(cand)
            if isinstance(data, dict):
                return data
        except Exception:
            return None
    return None


def _extract_json_list_from_text(text: str) -> list[Any] | None:
    """
    兼容两种常见结构：
    - 直接输出 JSON 数组：["a","b"]
    - 输出 JSON 对象包一层：{"keywords":[...]} / {"items":[...]}
    """

    arr = _extract_json_array(text)
    if isinstance(arr, list):
        return arr
    obj = _extract_json_object(text)
    if isinstance(obj, dict):
        for k in ("keywords", "items", "result", "keep_ranks", "insights", "proposals"):
            v = obj.get(k)
            if isinstance(v, list):
                return v
    return None


def _parse_keep_ranks_from_text(text: str) -> tuple[list[int], bool]:
    """
    解析 keep_ranks：
    - 返回 (keep_ranks, ok)
    - ok=True 表示成功解析出 keep_ranks 字段（即使为空数组也算 ok）
    """

    data = _extract_json_object(text or "")
    if isinstance(data, dict):
        if "keep_ranks" not in data:
            return ([], False)
        xs = data.get("keep_ranks")
        if not isinstance(xs, list):
            return ([], False)
    else:
        # 兼容：直接输出数组 [1,2,3]
        arr = _extract_json_array(text or "")
        if not isinstance(arr, list):
            return ([], False)
        xs = arr

    out: list[int] = []
    for x in xs:
        try:
            out.append(int(str(x).strip()))
        except Exception:
            continue
    return (out, True)


def _compact_rank_list(ranks: list[int], *, limit: int = 48) -> str:
    if not ranks:
        return "[]"
    uniq = []
    seen: set[int] = set()
    for r in ranks:
        if r in seen:
            continue
        seen.add(r)
        uniq.append(r)
    if len(uniq) <= limit:
        return json.dumps(uniq, ensure_ascii=False)
    return json.dumps(uniq[:limit], ensure_ascii=False)[:-1] + ", …]"


def _keep_ranks_allowed_from_items(items: list[dict[str, Any]]) -> list[int]:
    allowed: list[int] = []
    seen: set[int] = set()
    for it in items or []:
        r = it.get("rank")
        if not isinstance(r, int):
            continue
        if r in seen:
            continue
        seen.add(r)
        allowed.append(r)
    return allowed


def _pick_keep_ranks_with_retry(
    *,
    op: str,
    keyword: str,
    items: list[dict[str, Any]],
    system: dict[str, Any],
    user_base: dict[str, Any],
    cfg: dict[str, Any],
    keep_limit: int,
    require_non_empty: bool,
    min_required: int = 1,
    max_attempts: int = 3,
    repair_reasoning_split: str = "0",
) -> list[int]:
    """
    keep_ranks 结构化输出重试（禁止 mock、禁止静默兜底）：
    - attempt 1..max_attempts：主调用重试（带上一次问题 + 原始输出片段）
    - 最后一次：格式修复型重试（要求把原始输出整理成严格 JSON）
    - 如果 require_non_empty=True，则空数组视为失败并继续重试
    - keep_ranks 必须取自候选的 `rank` 字段（而不是数组下标）
    """

    allowed_ranks = _keep_ranks_allowed_from_items(items)
    allowed_set = set(allowed_ranks)

    if not items:
        return []
    if not allowed_set:
        raise RuntimeError(f"{op}: 候选缺少有效 rank，无法让 LLM 输出 keep_ranks")

    effective_min_required = max(0, int(min_required or 0))
    effective_min_required = min(effective_min_required, max(0, int(keep_limit or 0)))
    effective_min_required = min(effective_min_required, len(allowed_set))
    if require_non_empty:
        effective_min_required = max(1, effective_min_required)

    local_cfg: dict[str, Any] = {
        **cfg,
        "minimax_temperature": "0",
        "llm_response_format": "json_object",
        "minimax_reasoning_split": "0",
    }

    last_text = ""
    last_issue = ""

    def _validate(keep: list[int]) -> list[int]:
        out: list[int] = []
        seen: set[int] = set()
        for k in keep:
            if k in allowed_set and k not in seen:
                out.append(k)
                seen.add(k)
                if len(out) >= max(1, int(keep_limit or 0)):
                    break
        return out

    for attempt in range(1, max(1, max_attempts) + 1):
        if attempt == 1:
            user_msg = user_base
        else:
            snippet = (last_text or "").strip().replace("\n", " ")
            snippet = snippet[:1200]
            if require_non_empty or effective_min_required > 0:
                need_n = max(1, effective_min_required)
                min_hint = f"至少返回 {need_n} 个 rank。"
            else:
                min_hint = "可以返回空数组。"
            user_msg = {
                "role": "user",
                "content": (
                    f"上次输出存在问题：{last_issue or '无法解析/无有效 keep_ranks'}\n"
                    "现在请严格按要求输出。\n"
                    "规则：\n"
                    "1) 只输出一个 JSON 对象，且只能包含 keep_ranks 字段（整数数组）。\n"
                    "2) keep_ranks 必须从候选的 rank 字段取值（不要输出候选数组下标）。\n"
                    f"3) 可选 rank：{_compact_rank_list(allowed_ranks)}\n"
                    f"4) {min_hint}\n"
                    "不要解释、不要 Markdown、不要代码块、不要 <think>。\n"
                    "格式：{\"keep_ranks\":[1,2,3]}。\n\n"
                    f"词条：{keyword}\n"
                    f"候选(JSON)：{json.dumps(items, ensure_ascii=False)}\n"
                    f"上次原始输出：{snippet}"
                ),
            }

        debug_log(
            f"op={op} attempt={attempt} keyword={keyword!r} candidates={len(items)} keep={keep_limit}",
            cfg=cfg,
        )
        text = _llm_chat([system, user_msg], cfg=local_cfg)
        last_text = text or ""
        keep_raw, ok = _parse_keep_ranks_from_text(last_text)
        keep_valid = _validate(keep_raw)

        if ok and (keep_valid or not require_non_empty):
            if effective_min_required > 0 and len(keep_valid) < effective_min_required:
                # 数量不足：继续重试（不是兜底）
                last_issue = f"keep_ranks 数量不足（需要≥{effective_min_required}）"
                continue
            return keep_valid

        # 记录问题，供下一次重试引用
        if not ok:
            last_issue = "输出不是可解析 JSON，或缺少 keep_ranks 数组"
        elif require_non_empty and not keep_raw:
            last_issue = "keep_ranks 为空数组"
        elif keep_raw and effective_min_required > 0 and len(keep_valid) < effective_min_required:
            last_issue = f"keep_ranks 数量不足（有效={len(keep_valid)}，需要≥{effective_min_required}）"
        elif keep_raw and not keep_valid:
            last_issue = (
                f"keep_ranks={_compact_rank_list(keep_raw)} 不在候选 rank 中（可选 rank={_compact_rank_list(allowed_ranks)}）"
            )
        else:
            last_issue = "未得到可用 keep_ranks"

    # 最后：修复型重试，把原始输出整理成严格 JSON（仍是真实 LLM 调用，不是兜底）
    if require_non_empty or effective_min_required > 0:
        need_n = max(1, effective_min_required)
        min_hint = f"必须至少返回 {need_n} 个 rank。"
    else:
        min_hint = "可以返回空数组。"
    repair_prompt = {
        "role": "user",
        "content": (
            "把下面内容整理成严格 JSON 对象，只能包含 keep_ranks 字段。\n"
            "keep_ranks 必须是整数数组，且必须从候选的 rank 字段取值（不要输出数组下标）。\n"
            f"可选 rank：{_compact_rank_list(allowed_ranks)}\n"
            f"{min_hint}\n"
            "只输出 JSON，不要任何解释/Markdown/代码块/<think>。\n\n"
            f"词条：{keyword}\n"
            f"候选(JSON)：{json.dumps(items, ensure_ascii=False)}\n"
            f"上次问题：{last_issue}\n"
            f"原始内容：{(last_text or '')[:4000]}"
        ),
    }
    repaired = _llm_chat(
        [system, repair_prompt],
        cfg={**local_cfg, "minimax_reasoning_split": str(repair_reasoning_split).strip() or "0"},
    )
    keep_raw2, ok2 = _parse_keep_ranks_from_text(repaired or "")
    keep_valid2 = _validate(keep_raw2)
    if ok2 and (keep_valid2 or not require_non_empty):
        if effective_min_required > 0 and len(keep_valid2) < effective_min_required:
            snippet = (repaired or last_text or "").strip().replace("\n", " ")[:280]
            raise RuntimeError(
                f"LLM keep_ranks 数量不足（需要≥{effective_min_required}，得到={len(keep_valid2)}）：{snippet!r}"
            )
        return keep_valid2

    snippet = (repaired or last_text or "").strip().replace("\n", " ")[:280]
    if require_non_empty:
        raise RuntimeError(f"LLM 未能选出任何候选（期望 keep_ranks 非空 JSON）：{snippet!r}")
    raise RuntimeError(f"LLM keep_ranks 输出不合规（期望 keep_ranks JSON）：{snippet!r}")


def llm_pick_hotspot(
    *,
    candidates: list[dict[str, Any]],
    excluded_keywords: list[str],
    cfg: dict[str, Any],
) -> dict[str, Any]:
    """
    从热榜候选中选择**唯一一个**热点（用于本次跑全链路）。

    candidates: [{id, platform, platform_rank, keyword, hot?}]
    返回：{keyword, reason, platform, platform_rank, id}
    """

    # 只取前 N 个候选，避免喂太多导致不稳定
    max_input = int(cfg.get("hot_keyword_llm_input_limit") or 50)
    in_candidates: list[dict[str, Any]] = []
    for it in (candidates or []):
        if not isinstance(it, dict):
            continue
        kid = str(it.get("id") or "").strip()
        kw = str(it.get("keyword") or "").strip()
        if not kid or not kw:
            continue
        in_candidates.append(
            {
                "id": kid,
                "platform": str(it.get("platform") or "").strip(),
                "platform_rank": int(it.get("platform_rank") or 0),
                "keyword": kw,
                "hot": it.get("hot"),
            }
        )
        if len(in_candidates) >= max(1, max_input):
            break

    allow_ids = {it["id"] for it in in_candidates}
    excluded_lines = ""
    for kw in excluded_keywords or []:
        kw = str(kw).strip()
        if kw:
            excluded_lines += f"数据库已经有了《{kw}》热点，不要选择这个。\n"

    system = {
        "role": "system",
        "content": (
            "你是一个只输出 JSON 的程序。严格遵守：\n"
            "1) 只输出 JSON，不要解释，不要 Markdown，不要代码块，不要 <think>。\n"
            "2) 输出必须是 JSON 对象，且只包含字段 pick_id 与 reason。\n"
            "3) pick_id 必须从 candidates 的 id 中选择。\n"
            "4) reason 必须非常简短，1-2 句话，尽量控制在 80 个中文字符以内。\n"
        ),
    }

    user_base = (
        "任务：从 candidates 中选择**唯一一个**热点（只选 1 个）。\n"
        "账号偏好：法律/法治，偏法规解读、典型案例分析、司法动态、严谨、可验证；尽量选择普通人更可能关心/有疑问/有普法价值的话题。\n"
        "素材约束：下游需要在主流资讯搜索里搜到足够的真实文章素材，所以必须优先选择“新闻型、报道型、可被主流媒体覆盖”的题目。\n"
        "降级规则：像微博原话、聊天句、情绪化长句、纯梗、纯社媒口号这类词条，即使热度高，也应明显降级，因为往往缺少可验证的资讯素材。\n"
            "热榜偏好（从高到低）：抖音 Top3 > 其他抖音（优先靠前）> 微博 Top3 > 其他微博（优先靠前）。排名太靠后（例如 >10）的尽量不选，除非前面都明显不合适。\n"
        "排除项：明显属于官方硬推/宣传口径/主旋律正能量传播的话题不要选，包括但不限于：官方纪念日、主题宣传日、官媒统一口径、领导人讲话/勉励/贺信/指示、政策表述复述、机构成就宣传、明显缺乏民间自发讨论而主要依靠官方议程带热的话题，以及涉及美军/军事、中外政治对抗、台海南海、社会恶性事件等敏感话题。\n"
        "识别原则：像“中国航天日”“总书记勉励中国航天奋斗圆梦”这类标题，即使热度高、排名靠前，也应视为优先排除的官方宣发型热点；优先选择真实事件驱动、公众有疑问、有争议、有信息差、能展开法律解读的话题。\n"
        "只有在 candidates 几乎全是这类官方宣发型话题时，才退而求其次选择其中相对更有真实公众讨论空间的一项，并在 reason 中明确说明局限。\n"
        "如果“法律匹配度高但缺少资讯素材”和“匹配度略低但主流媒体报道充分”之间需要二选一，优先选择后者，保证工作流能继续产出。\n"
        "去重规则：不要选择数据库里已存在的热点。\n"
        + excluded_lines
        + "\n输出要求：只输出 JSON 对象，格式：\n"
        '{"pick_id":"weibo-1","reason":"1-2句话简述选择理由"}\n\n'
        f"candidates(JSON数组)：{json.dumps(in_candidates, ensure_ascii=False)}"
    )

    local_cfg: dict[str, Any] = {
        **cfg,
        "minimax_temperature": "0",
        "llm_response_format": "json_object",
        "minimax_reasoning_split": "0",
    }
    debug_log(f"op=pick_hotspot candidates={len(in_candidates)} excluded={len(excluded_keywords or [])}", cfg=cfg)

    last_stage = "selection"
    last_messages: list[dict[str, Any]] = []
    last_text = ""
    last_issue = ""

    def _try_parse(text: str) -> dict[str, Any] | None:
        obj = _extract_json_object(text or "")
        if not isinstance(obj, dict):
            return None
        pick_id = str(obj.get("pick_id") or "").strip()
        reason = str(obj.get("reason") or "").strip()
        if pick_id in allow_ids and reason and "..." not in reason:
            chosen = next((x for x in in_candidates if x["id"] == pick_id), None)
            if chosen:
                return {
                    "id": pick_id,
                    "keyword": chosen["keyword"],
                    "platform": chosen.get("platform") or "",
                    "platform_rank": int(chosen.get("platform_rank") or 0),
                    "reason": reason,
                }
        return None

    for attempt in range(1, 6):
        if attempt == 1:
            last_messages = [system, {"role": "user", "content": user_base}]
        else:
            snippet = (last_text or "").strip().replace("\n", " ")
            snippet = snippet[:1200]
            last_messages = [
                system,
                {
                    "role": "user",
                    "content": (
                        f"上次输出存在问题：{last_issue or '无法解析或字段不合规'}\n"
                        "现在请严格按要求只输出 JSON 对象。\n"
                        "规则：\n"
                        "1) 只输出 JSON，不要解释，不要 Markdown，不要代码块，不要 <think>。\n"
                        "2) 输出必须是 JSON 对象，且只包含字段 pick_id 与 reason。\n"
                        "3) pick_id 必须从 candidates 的 id 中选择。\n\n"
                        f"{user_base}\n\n"
                        f"上次原始输出：{snippet}"
                    ),
                },
            ]

        last_text = _llm_chat(last_messages, cfg=local_cfg) or ""
        parsed = _try_parse(last_text)
        if parsed is not None:
            return parsed

        obj = _extract_json_object(last_text)
        if not isinstance(obj, dict):
            last_issue = "输出不是可解析 JSON 对象"
        else:
            pick_id = str(obj.get("pick_id") or "").strip()
            reason = str(obj.get("reason") or "").strip()
            if not pick_id or not reason:
                last_issue = "缺少 pick_id 或 reason"
            elif pick_id not in allow_ids:
                last_issue = f"pick_id 不在 candidates 中：{pick_id!r}"
            else:
                last_issue = "字段内容不合规"

    repair_prompt = {
        "role": "user",
        "content": (
            "把下面内容整理成严格 JSON 对象，只能包含 pick_id 与 reason 字段。\n"
            "pick_id 必须从 candidates 的 id 中选择。\n"
            "只输出 JSON，不要任何解释/Markdown/代码块/<think>。\n\n"
            f"{user_base}\n\n"
            f"上次问题：{last_issue}\n"
            f"原始内容：{(last_text or '')[:4000]}"
        ),
    }
    repaired = _llm_chat([system, repair_prompt], cfg={**local_cfg, "minimax_reasoning_split": "1"}) or ""
    parsed = _try_parse(repaired)
    if parsed is not None:
        return parsed

    last_response = repaired or last_text or ""
    raise RuntimeError(
        "LLM 热点选择输出不合规（期望 pick_id+reason JSON）"
        f"\nlast_stage={last_stage}"
        f"\nlast_messages={json.dumps(last_messages, ensure_ascii=False)}"
        f"\nlast_response={last_response}"
    )


def llm_filter_keywords(*, keywords: list[str], cfg: dict[str, Any]) -> list[str]:
    max_input = int(cfg.get("hot_keyword_llm_input_limit") or 50)
    max_output = int(cfg.get("hot_keyword_llm_output_limit") or 30)

    # 避免一次性喂 100+ 关键词导致模型长时间推理/超时：只取前 max_input
    # 上游热榜通常已按热度排序，取前 N 仍有代表性。
    in_keywords = [str(k).strip() for k in (keywords or []) if str(k).strip()]
    in_keywords = in_keywords[: max(1, max_input)]
    allow_set = set(in_keywords)

    # MiniMax 在部分场景下会输出英文分析或把最终答案塞进"思考内容"，导致 content 为空或不合规。
    # 这里用 system 指令 + 低温度 + 较小 max_tokens 强约束结构化输出。
    ranked_items = [{"rank": i + 1, "keyword": k} for i, k in enumerate(in_keywords)]

    system = {
        "role": "system",
        "content": (
            "你是一个只输出 JSON 的程序。严格遵守：\n"
            "1) 只输出 JSON（不要解释、不要 Markdown、不要代码块、不要英文、不要思考过程）。\n"
            "2) 如果无法选择，输出 {\"keep_ranks\":[]}。\n"
            "3) 输出必须是 JSON 对象，且只包含字段 keep_ranks（整数数组，1-based）。\n"
        ),
    }

    user_base = (
        "任务：从给定热点关键词中，挑选出具备“法律/法治”延展潜力的词条（剔除纯娱乐八卦）。\n"
        f"要求：最多保留 {max_output} 条；必须从输入中选择，不能编造。\n"
        "输出：严格 JSON 对象，例如 {\"keep_ranks\":[1,4,6]}。\n"
        f"候选(JSON数组)：{json.dumps(ranked_items, ensure_ascii=False)}"
    )

    # 最多重试 3 次：
    # - 第 1 次：按当前配置调用
    # - 第 2 次：强制 minimax_reasoning_split=0（某些情况下 content 可能为空）
    # - 第 3 次：回到 reasoning_split=1，再次强制纠正输出格式
    last_text = ""
    for attempt in range(1, 4):
        # 本节点优先不拆思考链路，避免 content 为空；并强制低温度。
        local_cfg: dict[str, Any] = {
            **cfg,
            "minimax_temperature": "0",
            # 允许模型在输出前"短暂思考"，但要保证最终 JSON 能落地
            # MiniMax OpenAI 兼容：强制 JSON（对象）输出，提升稳定性
            "llm_response_format": "json_object",
        }
        if attempt in (1, 2):
            local_cfg["minimax_reasoning_split"] = "0"
        else:
            local_cfg["minimax_reasoning_split"] = "1"

        debug_log(
            f"op=filter_keywords attempt={attempt} in={len(in_keywords)} max_out={max_output} "
            f"reasoning_split={str(local_cfg.get('minimax_reasoning_split')).strip()}",
            cfg=cfg,
        )
        if attempt == 1:
            messages = [system, {"role": "user", "content": user_base}]
        elif attempt == 2:
            messages = [
                system,
                {"role": "user", "content": "上次输出不合规。请严格按要求只输出 JSON 数组。\n" + user_base},
            ]
        else:
            # 第 3 次进一步强调只允许 JSON 对象（response_format 也会强约束）
            messages = [
                system,
                {
                    "role": "user",
                    "content": (
                        "上次仍不合规。现在只允许输出一个 JSON 对象，且必须含 keywords 字段。\n"
                        + user_base
                    ),
                },
            ]

        text = _llm_chat(messages, cfg=local_cfg)
        last_text = text or ""

        ranks_any = _extract_json_list_from_text(text or "")
        if isinstance(ranks_any, list):
            ranks: list[int] = []
            for x in ranks_any:
                try:
                    ranks.append(int(str(x).strip()))
                except Exception:
                    continue
            ranks = [r for r in ranks if 1 <= r <= len(in_keywords)]
            seen_ranks: set[int] = set()
            out: list[str] = []
            for r in ranks:
                if r in seen_ranks:
                    continue
                seen_ranks.add(r)
                out.append(in_keywords[r - 1])
                if len(out) >= max_output:
                    break
            if out:
                return out

        # 修复：有时模型会输出大量思考/解释，导致主输出缺少 keep_ranks。这里再走一次"只整理 JSON"的短提示。
        repair_prompt = {
            "role": "user",
            "content": (
                "只输出 JSON 对象，且只能包含 keep_ranks 字段。\n"
                "从下面候选中选出具备“法律/法治”延展潜力的条目（剔除纯娱乐八卦），最多 10 条。\n"
                "输出格式：{\"keep_ranks\":[1,4,6]}。\n\n"
                f"候选(JSON数组)：{json.dumps(ranked_items, ensure_ascii=False)}"
            ),
        }
        repaired = _llm_chat(
            [repair_prompt],
            cfg={**local_cfg, "minimax_reasoning_split": "0"},
        )
        ranks_any2 = _extract_json_list_from_text(repaired or "")
        if isinstance(ranks_any2, list):
            ranks: list[int] = []
            for x in ranks_any2:
                try:
                    ranks.append(int(str(x).strip()))
                except Exception:
                    continue
            ranks = [r for r in ranks if 1 <= r <= len(in_keywords)]
            seen_ranks: set[int] = set()
            out: list[str] = []
            for r in ranks:
                if r in seen_ranks:
                    continue
                seen_ranks.add(r)
                out.append(in_keywords[r - 1])
                if len(out) >= max_output:
                    break
            if out:
                return out

    # 两次都拿不到合规 JSON：抛错，避免"假流程"静默继续
    snippet = (last_text or "").strip().replace("\n", " ")[:280]
    raise RuntimeError(f"LLM 关键词筛选输出不合规（期望 JSON 数组）：{snippet!r}")


def llm_deep_dive(
    *, keywords: list[str], materials: list[Material], cfg: dict[str, Any]
) -> list[DeepInsight]:
    prompt = {
        "role": "user",
        "content": f"请对热点 {keywords} 做深度挖掘，并输出3个切入点。",
    }
    text = _llm_chat([prompt], cfg=cfg)
    return [{"angle": "洞察", "details": text}]


def llm_generate_proposals(
    *,
    keywords: list[str],
    materials: list[Material],
    proposal_candidates: list[TopicProposal],
    article_analysis: str,
    cfg: dict[str, Any],
) -> list[TopicProposal]:
    # 该节点必须真实调用 LLM 且输出结构化 JSON，避免"占位/假数据"。
    if not proposal_candidates:
        raise RuntimeError("proposal_candidates 为空，无法做排序筛选")

    candidate_items = []
    for item in proposal_candidates:
        candidate_items.append(
            {
                "candidate_id": str(item.get("candidate_id") or item.get("proposal_id") or "").strip(),
                "title": str(item.get("title") or "").strip(),
                "thesis": str(item.get("thesis") or "").strip(),
            }
        )

    base_content = (
        "你是一个法律/法治方向的短视频选题策划。\n"
        "下面有 10 套已经生成好的选题候选，请你不要新编选题，只从候选中做筛选和排序。\n"
        "请按照以下优先级综合排序：\n"
        "1) 话题度和传播潜力\n"
        "2) 与账号定位的匹配度（法律/法治方向，偏法规解读、案例分析、司法动态）\n"
        "3) 是否直接回应观众对当前热点最想知道的问题\n"
        "4) 是否容易继续搜集到可验证素材，适合后续脚本生产\n"
        "请选出前 5 个，并输出排序结果。每个结果包含：\n"
        "- proposal_id: \"A\"/\"B\"/\"C\"/\"D\"/\"E\"（表示你的排序名次）\n"
        "- candidate_id: 原候选 ID\n"
        "- rank: 排名，1-5\n"
        "- score: 0-100 的综合分\n"
        "- title: 候选标题原样返回\n"
        "- thesis: 候选核心论点原样返回\n"
        "- outline: 为进入前五的题目补充 4-6 条内容大纲，字符串数组\n"
        "- selection_reason: 1-2 句话，解释为什么它值得进前五\n"
        "要求：中文、严谨、可验证，不八卦，不要改写候选的基本事实表达。\n"
        "输出要求：只输出 JSON 对象，格式为 {\"proposals\":[...]}（不要解释、不要 Markdown、不要思考过程）。\n\n"
        f"关键词：{json.dumps(keywords, ensure_ascii=False)}\n"
        f"热点详细事件：{(article_analysis or '')[:2500]}\n"
        f"素材摘要(最多10条)：{json.dumps(materials[:10], ensure_ascii=False)}\n"
        f"候选选题(JSON)：{json.dumps(candidate_items, ensure_ascii=False)}"
    )

    system = {
        "role": "system",
        "content": (
            "你是一个只输出 JSON 的程序。严格遵守：\n"
            "1) 只输出 JSON，不要解释，不要 Markdown，不要 <think>，不要思考过程。\n"
            "2) 输出必须是 JSON 对象，且必须包含 proposals 字段（数组）。\n"
        ),
    }

    def _extract_proposals(text: str) -> list[dict[str, Any]] | None:
        obj = _extract_json_object(text or "")
        if isinstance(obj, dict) and isinstance(obj.get("proposals"), list):
            items = [x for x in obj.get("proposals") if isinstance(x, dict)]
            return items or None
        return None

    last_text = ""
    data: list[dict[str, Any]] | None = None
    for attempt in range(1, 4):
        local_cfg: dict[str, Any] = {
            **cfg,
            "minimax_temperature": "0",
            "llm_response_format": "json_object",
            # 默认不拆思考链路，避免 content 为空；第二次再尝试拆分
            "minimax_reasoning_split": "0",
        }
        if attempt == 2:
            local_cfg["minimax_reasoning_split"] = "1"
        prompt = {
            "role": "user",
            "content": base_content if attempt == 1 else ("上次输出不合规，请严格只输出 JSON。\n" + base_content),
        }
        text = _llm_chat([system, prompt], cfg=local_cfg)
        last_text = text or ""
        data = _extract_proposals(last_text)
        if isinstance(data, list) and data:
            break

        # 修复：把非 JSON 输出强制整理成 JSON（基于同样输入重生成）
        repair_prompt = {
            "role": "user",
            "content": (
                "只输出 JSON 对象，格式必须是：\n"
                "{\"proposals\":[{\"proposal_id\":\"A\",\"candidate_id\":\"T01\",\"rank\":1,\"score\":95,"
                "\"title\":\"...\",\"thesis\":\"...\",\"outline\":[\"...\"],\"selection_reason\":\"...\"},"
                "{\"proposal_id\":\"B\",...}]}\n"
                "不要任何解释。\n\n"
                + base_content
            ),
        }
        repaired = _llm_chat([system, repair_prompt], cfg={**local_cfg, "minimax_reasoning_split": "0"})
        last_text = repaired or last_text
        data = _extract_proposals(last_text)
        if isinstance(data, list) and data:
            break

    if not isinstance(data, list) or not data:
        snippet = (last_text or "").strip().replace("\n", " ")[:280]
        raise RuntimeError(f"LLM 选题生成输出不合规（期望 proposals JSON）：{snippet!r}")

    out: list[TopicProposal] = []
    seen_ids: set[str] = set()
    seen_candidates: set[str] = set()
    valid_ids = ("A", "B", "C", "D", "E")
    for it in data:
        if not isinstance(it, dict):
            continue
        pid = str(it.get("proposal_id") or "").strip().upper()
        candidate_id = str(it.get("candidate_id") or "").strip().upper()
        if pid not in valid_ids or pid in seen_ids or not candidate_id or candidate_id in seen_candidates:
            continue
        title = str(it.get("title") or "").strip()
        thesis = str(it.get("thesis") or "").strip()
        selection_reason = str(it.get("selection_reason") or "").strip()
        outline_raw = it.get("outline")
        if not title or not thesis or not selection_reason or not isinstance(outline_raw, list):
            continue
        outline = [str(x).strip() for x in outline_raw if str(x).strip()]
        if not outline:
            continue
        try:
            rank = int(str(it.get("rank") or "").strip())
        except Exception:
            rank = 0
        try:
            score = int(str(it.get("score") or "").strip())
        except Exception:
            score = 0
        if rank < 1 or rank > 5:
            continue
        if score < 0 or score > 100:
            continue
        out.append(
            {
                "proposal_id": pid,
                "candidate_id": candidate_id,
                "rank": rank,
                "score": score,
                "title": title,
                "thesis": thesis,
                "outline": outline,
                "selection_reason": selection_reason,
            }
        )
        seen_ids.add(pid)
        seen_candidates.add(candidate_id)

    if len(out) == 5:
        return sorted(out, key=lambda x: x["rank"])

    snippet = (last_text or "").strip().replace("\n", " ")[:280]
    raise RuntimeError(f"LLM 选题排序输出字段缺失或数量不足（期望 5 条 proposals）：{snippet!r}")


def llm_build_material_queries(
    *,
    proposal: dict[str, Any],
    hot_keyword: str,
    run_date: str,
    cfg: dict[str, Any],
) -> list[str]:
    """
    基于"已选题目"（通常较吸引人/偏模糊）构造更可检索的素材搜索 query（用于补充信息检索）。

    约束（硬）：必须真实调用 LLM 且输出结构化 JSON；不允许静默兜底。
    输出：严格返回 3 个 query（字符串数组）。
    """

    title = str(proposal.get("title") or "").strip()
    thesis = str(proposal.get("thesis") or "").strip()
    outline_raw = proposal.get("outline") or []
    outline = [str(x).strip() for x in outline_raw if str(x).strip()]
    hot_kw = str(hot_keyword or "").strip()
    run_date = str(run_date or "").strip()
    hot_titles_raw = proposal.get("hot_titles") or proposal.get("hotlist_titles") or []
    hot_titles = [str(x).strip() for x in (hot_titles_raw or []) if str(x).strip()]

    if not title:
        raise RuntimeError("llm_build_material_queries: proposal.title 为空")

    # 针对软件/互联网产品类热点：强制要求至少 1 条 query 带官方域名锚点，
    # 否则 Bing 很容易被"企业/公司/腾讯"等泛词带偏到知乎/问答/无关论坛。
    joined_ctx = " ".join([title, thesis, hot_kw, " ".join(outline)]).lower()
    need_wecom_domain = ("企业微信" in joined_ctx) or ("wecom" in joined_ctx)
    # "微信"本身太泛，但如果选题里明确在讨论微信的办公化/互通/企业协作，也希望带官方域名锚点。
    need_wechat_domain = (("微信" in joined_ctx) and ("企业微信" not in joined_ctx)) or ("wechat" in joined_ctx)

    base_content = (
        "你是一个内容研究助理。你的任务不是复述热榜，而是为“已选题目”补齐可验证的背景资料。\n"
        "你需要把题目拆成“除了热榜本身以外，还缺什么信息？”并据此生成搜索 query。\n"
        "这些 query 会用于网页搜索（文章）和 B 站站内搜索（视频），因此必须能搜到真实资料。\n\n"
        "请输出 3 个 query，分别对应 3 类“补充信息”：\n"
        "1) 官方/一手材料：公告/通报/通知/政策/标准/指南/白皮书/更新日志/版本记录/发布说明\n"
        "2) 量化证据/数据：统计数据/研究报告/论文/测评/对比实验/监管数据/投诉数据/财报/指标\n"
        "3) 争议与回应链：当事方回应/监管回应/媒体调查/关键时间线/辟谣与核查线索\n\n"
        "硬规则（必须遵守）：\n"
        "- 不要输出与热榜词条重复/近似的 query：你的 query 必须与热榜标题列表明显不同，避免“换个说法搜同一条”。\n"
        "- 每个 query 必须包含至少 2 个可验证锚点：例如（机构/部门/公司/产品正式名）+（公告/通报/数据/报告/标准/更新日志/版本号/时间等）。\n"
        "- 每个 query 必须是中文为主的短语，不要问句、不带引号、不写 Markdown。\n"
        "- 每个 query 8-30 个汉字左右（过短搜不准，过长影响检索）。\n"
        "- 3 个 query 必须互不重复，并尽量覆盖不同资料类型。\n"
        "- 允许使用更正式的表达替代 hot_keyword（例如把网民昵称换成官方称谓/机构全称），但不能只做同义替换。\n"
        "- 优先选择“能产生增量信息”的关键词：能导向公告/报告/数据/标准/接口文档/更新日志/审计/处罚/监管文件等。\n"
        "- 禁止输出泛词堆砌（如“深度解析/影响很大/全面调查/机制揭秘”）。\n\n"
        + (
            ""
            if not hot_titles
            else (
                "热榜标题列表（用于排除重复，请勿输出与其重复或仅轻微改写的 query）：\n"
                f"{json.dumps(hot_titles[:30], ensure_ascii=False)}\n\n"
            )
        )
        + (
            ""
            if not need_wecom_domain
            else (
                "企业微信/WeCom 强制规则：3 条 query 中至少 1 条必须以域名锚点开头：\n"
                "- `work.weixin.qq.com ...`\n"
                "- 或 `open.work.weixin.qq.com ...`\n"
                "（域名必须原样包含，并放在 query 的最开头）。\n"
            )
        )
        + (
            ""
            if not need_wechat_domain
            else (
                "微信/WeChat 强制规则：3 条 query 中至少 1 条必须包含域名锚点 `weixin.qq.com`（必须原样包含）。\n"
            )
        )
        + "\n"
        "输出要求：只输出 JSON 对象，格式严格为：\n"
        "{\"queries\":[\"q1\",\"q2\",\"q3\"]}\n\n"
        f"run_date: {run_date}\n"
        f"hot_keyword: {hot_kw}\n"
        f"proposal.title: {title}\n"
        f"proposal.thesis: {thesis}\n"
        f"proposal.outline: {json.dumps(outline[:8], ensure_ascii=False)}\n"
    )

    system = {
        "role": "system",
        "content": (
            "你是一个只输出 JSON 的程序。严格遵守：\n"
            "1) 只输出 JSON，不要解释，不要 Markdown，不要 <think>。\n"
            "2) 输出必须是 JSON 对象，且只能包含 queries 字段（数组）。\n"
            "3) queries 必须严格包含 3 个字符串。\n"
        ),
    }

    def _edit_distance(a: str, b: str) -> int:
        """Levenshtein 距离，用于判断两个 query 是否近似重复。"""
        if len(a) < len(b):
            a, b = b, a
        # 优化：只保留两行
        prev = list(range(len(b) + 1))
        curr = [0] * (len(b) + 1)
        for i, ca in enumerate(a, 1):
            curr[0] = i
            for j, cb in enumerate(b, 1):
                curr[j] = (
                    prev[j - 1]
                    if ca == cb
                    else 1 + min(prev[j], curr[j - 1], prev[j - 1])
                )
            prev, curr = curr, prev
        return prev[-1]

    def _validate_queries(obj: dict[str, Any]) -> tuple[list[str] | None, str]:
        qs = obj.get("queries")
        if not isinstance(qs, list):
            return None, "queries 字段不是数组"
        if len(qs) != 3:
            return None, f"queries 长度应为 3，实际为 {len(qs)}"
        out: list[str] = []
        seen: set[str] = set()
        for idx, q in enumerate(qs):
            s = re.sub(r"\s+", " ", str(q or "")).strip()
            s = s.replace("\n", " ").replace("\r", " ").strip()
            if not s:
                return None, f"query[{idx}] 为空"
            # 避免过短/过长：短会搜不准，长会降低检索效果（尤其是 B 站站内搜索）
            if len(s) < 6:
                return None, f"query[{idx}] 过短（{len(s)} 字符，需 ≥6）：{s!r}"
            if len(s) > 64:
                return None, f"query[{idx}] 过长（{len(s)} 字符，需 ≤64）：{s[:40]!r}..."
            if any(x in s for x in ("\"", "“", "”", "```")):
                return None, f"query[{idx}] 含引号/Markdown：{s!r}"
            key = re.sub(r"\s+", "", s)
            if key in seen:
                return None, f"query[{idx}] 与前面的 query 重复：{s!r}"
            # 编辑距离 ≤2 判为与热榜标题近似重复（只做精确+极小变体去重，不再用包含关系）
            if hot_titles:
                hot_norms = [re.sub(r"\s+", "", str(x)) for x in hot_titles if str(x)]
                for hn in hot_norms:
                    if hn and _edit_distance(key, hn) <= 2:
                        return None, f"query[{idx}] 与热榜标题近似（编辑距离≤2）：query={s!r} hot={hn!r}"
            seen.add(key)
            out.append(s)

        if len(out) != 3:
            return None, "queries 最终数量不为 3"

        if need_wecom_domain:
            lowered = [x.lower().strip() for x in out]
            if not any(
                x.startswith("work.weixin.qq.com") or x.startswith("open.work.weixin.qq.com")
                for x in lowered
            ):
                return None, "缺少企业微信域名锚点（需 work.weixin.qq.com 或 open.work.weixin.qq.com）"

        if need_wechat_domain:
            lowered = [x.lower() for x in out]
            if not any("weixin.qq.com" in x for x in lowered):
                return None, "缺少微信域名锚点（需 weixin.qq.com）"

        return out, ""

    last_text = ""
    last_issue = ""
    last_obj: dict[str, Any] | None = None
    for attempt in range(1, 4):
        local_cfg: dict[str, Any] = {
            **cfg,
            "minimax_temperature": "0",
            "llm_response_format": "json_object",
            "minimax_reasoning_split": "0",
        }
        prompt = {
            "role": "user",
            "content": base_content
            if attempt == 1
            else (f"上次输出不合规：{last_issue or '无法解析/字段不匹配'}。请严格只输出 JSON。\n\n" + base_content),
        }
        text = _llm_chat([system, prompt], cfg=local_cfg) or ""
        last_text = text
        obj = _extract_json_object(last_text) or {}
        last_obj = obj
        if isinstance(obj, dict):
            queries, reason = _validate_queries(obj)
            if queries:
                debug_log(f"op=build_material_queries ok queries={json.dumps(queries, ensure_ascii=False)}", cfg=cfg)
                return queries
            last_issue = reason or "缺少 queries 或 queries 不为 3 个有效字符串"
        else:
            last_issue = "LLM 输出无法解析为 JSON"

        # 修复型重试：要求模型把结果整理成严格 JSON
        snippet = (last_text or "").strip().replace("\n", " ")[:1200]
        repair_prompt = {
            "role": "user",
            "content": (
                "把你的输出整理成严格 JSON，只输出如下格式：\n"
                "{\"queries\":[\"q1\",\"q2\",\"q3\"]}\n"
                "约束：queries 必须正好 3 条，且每条是中文短语（不要问句/引号/Markdown）。\n\n"
                f"原始输出：{snippet}\n\n"
                + base_content
            ),
        }
        repaired = _llm_chat([system, repair_prompt], cfg=local_cfg) or ""
        last_text = repaired or last_text
        obj = _extract_json_object(last_text) or {}
        last_obj = obj
        if isinstance(obj, dict):
            queries, reason = _validate_queries(obj)
            if queries:
                debug_log(
                    f"op=build_material_queries repaired queries={json.dumps(queries, ensure_ascii=False)}",
                    cfg=cfg,
                )
                return queries
            last_issue = f"修复后仍失败: {reason}" if reason else "修复后仍无法得到严格 queries JSON"
        else:
            last_issue = "修复后 LLM 输出仍无法解析为 JSON"

    # 兜底：从最后一次提取到的 JSON 对象中宽松抽取 queries
    if isinstance(last_obj, dict):
        raw_qs = last_obj.get("queries")
        if isinstance(raw_qs, list):
            fallback: list[str] = []
            for q in raw_qs:
                s = re.sub(r"\s+", " ", str(q or "")).strip()
                s = s.replace("\n", " ").replace("\r", " ").strip()
                # 清洗：去引号、截断过长
                s = s.replace("\"", "").replace(""", "").replace(""", "").replace("`", "")
                if len(s) > 64:
                    s = s[:64].rstrip()
                if len(s) >= 4 and s not in fallback:
                    fallback.append(s)
            if len(fallback) >= 2:
                # 不足 3 条时补一个占位 query
                while len(fallback) < 3:
                    fallback.append(fallback[0] if fallback else title)
                fallback = fallback[:3]
                debug_log(
                    f"op=build_material_queries FALLBACK queries={json.dumps(fallback, ensure_ascii=False)}"
                    f" last_issue={last_issue!r}",
                    cfg=cfg,
                )
                return fallback

    snippet = (last_text or "").strip().replace("\n", " ")[:280]
    raise RuntimeError(f"LLM 查询词构建输出不合规（期望 queries JSON）：{snippet!r}")


def llm_summarize_keyword_materials(
    *,
    keyword_materials_by_keyword: dict[str, Any],
    keyword_materials_text: str,
    cfg: dict[str, Any],
) -> dict[str, Any]:
    """
    对每个 keyword 的"检索资料大段文本"做提炼输出。

    约束（硬）：必须真实调用 LLM 且输出结构化 JSON；不允许静默兜底。
    输出：
    - keyword_materials_summary_by_keyword: {keyword: summary}
    - keyword_materials_summary_text: 合并后的可读文本（按 keyword 分块）
    """

    raw_keys = list(keyword_materials_by_keyword.keys()) if isinstance(keyword_materials_by_keyword, dict) else []
    keywords = [str(k).strip() for k in raw_keys if str(k).strip()]
    if not keywords:
        return {"keyword_materials_summary_by_keyword": {}, "keyword_materials_summary_text": ""}

    max_input_chars = int(cfg.get("keyword_materials_llm_input_max_chars") or 24000)
    max_output_chars = int(cfg.get("keyword_materials_llm_output_max_chars") or 8000)

    payload_items: list[dict[str, Any]] = []
    for kw in keywords:
        group = keyword_materials_by_keyword.get(kw) or {}
        block = str((group or {}).get("text") or "").strip()
        if not block:
            block = keyword_materials_text
        block = _compact_llm_text(block, limit=max_input_chars)
        payload_items.append({"keyword": kw, "text": block})

    system = {
        "role": "system",
        "content": (
            "你是一个只输出 JSON 的程序。严格遵守：\n"
            "1) 只输出 JSON，不要解释，不要 Markdown，不要 <think>。\n"
            "2) 输出必须是 JSON 对象，且必须包含 summaries 字段（数组）。\n"
            "3) summaries 每项必须包含 keyword 与 summary 字段，且 keyword 必须来自输入 keyword 列表。\n"
        ),
    }

    base_content = (
        "你是一个资料提炼助手。下面给你每个 keyword 相关的“文章正文 + 视频字幕”合集。\n"
        "请你对每个 keyword 单独提炼摘要，要求：\n"
        "- 每个 keyword 一段 summary（220-420 字左右，中文）\n"
        "- 只提炼资料里出现过/可验证的内容；不确定就写“尚待核实/资料未覆盖”\n"
        "- 优先产出：关键机制/时间线/核心结论/常见误解澄清/与热点主线的关系\n"
        "- 不要写营销话术、不写八卦\n"
        "输出必须是 JSON 对象，格式严格为：\n"
        "{\"summaries\":[{\"keyword\":\"...\",\"summary\":\"...\"}, ...]}\n\n"
        f"keywords: {json.dumps(keywords, ensure_ascii=False)}\n"
        f"inputs: {json.dumps(payload_items, ensure_ascii=False)}\n"
    )

    def _validate(obj: dict[str, Any]) -> dict[str, str] | None:
        items = obj.get("summaries")
        if not isinstance(items, list):
            return None
        out: dict[str, str] = {}
        allowed = {k for k in keywords}
        for it in items:
            if not isinstance(it, dict):
                continue
            kw = str(it.get("keyword") or "").strip()
            summary = str(it.get("summary") or "").strip()
            if not kw or kw not in allowed or not summary:
                continue
            if kw in out:
                continue
            out[kw] = summary
        if len(out) != len(allowed):
            return None
        return out

    last_text = ""
    last_issue = ""
    for attempt in range(1, 4):
        local_cfg: dict[str, Any] = {
            **cfg,
            "minimax_temperature": "0",
            "llm_response_format": "json_object",
            "minimax_reasoning_split": "0",
        }
        prompt = {
            "role": "user",
            "content": base_content
            if attempt == 1
            else (f"上次输出不合规：{last_issue or '无法解析/字段不匹配'}。请严格只输出 JSON。\n\n" + base_content),
        }
        text = _llm_chat([system, prompt], cfg=local_cfg) or ""
        last_text = text
        obj = _extract_json_object(last_text) or {}
        summaries = _validate(obj) if isinstance(obj, dict) else None
        if summaries:
            blocks = []
            for kw in keywords:
                blocks.append(f"# {kw}\n{summaries.get(kw) or ''}".strip())
            merged = "\n\n".join(blocks).strip()
            merged = _compact_llm_text(merged, limit=max_output_chars)
            return {
                "keyword_materials_summary_by_keyword": summaries,
                "keyword_materials_summary_text": merged,
            }

        last_issue = "缺少 summaries 或 summaries 未覆盖全部 keywords"

        snippet = (last_text or "").strip().replace("\n", " ")[:1600]
        repair_prompt = {
            "role": "user",
            "content": (
                "把你的输出整理成严格 JSON，只输出如下格式：\n"
                "{\"summaries\":[{\"keyword\":\"...\",\"summary\":\"...\"}]}\n"
                "要求：必须覆盖所有 keywords；keyword 必须来自输入列表；summary 必须为中文段落。\n\n"
                f"原始输出：{snippet}\n\n"
                + base_content
            ),
        }
        repaired = _llm_chat([system, repair_prompt], cfg=local_cfg) or ""
        last_text = repaired or last_text
        obj = _extract_json_object(last_text) or {}
        summaries = _validate(obj) if isinstance(obj, dict) else None
        if summaries:
            blocks = []
            for kw in keywords:
                blocks.append(f"# {kw}\n{summaries.get(kw) or ''}".strip())
            merged = "\n\n".join(blocks).strip()
            merged = _compact_llm_text(merged, limit=max_output_chars)
            return {
                "keyword_materials_summary_by_keyword": summaries,
                "keyword_materials_summary_text": merged,
            }

    snippet = (last_text or "").strip().replace("\n", " ")[:280]
    raise RuntimeError(f"LLM keyword_materials 提炼输出不合规（期望 summaries JSON）：{snippet!r}")


def llm_generate_final_script(
    *,
    proposal: TopicProposal,
    materials: list[Material],
    article_analysis: str,
    video_analysis: str,
    article_extracts: list[dict[str, Any]] | None = None,
    video_transcripts: list[dict[str, Any]] | None = None,
    cfg: dict[str, Any],
) -> str:
    # 这里生成的是"口播文案"（详细视频解说脚本），不输出表格/分栏。
    # 注意：article_analysis 在本工作流中来自 `aggregate_article_text(_selected)`，
    # 是对热点"事情经过/时间线/争议点"的聚合总结，应作为主线事实依据。
    extracts = list(article_extracts or [])
    transcripts = list(video_transcripts or [])

    ref_articles: list[dict[str, Any]] = []
    for a in extracts[:8]:
        if not isinstance(a, dict):
            continue
        ref_articles.append(
            {
                "title": str(a.get("title") or "").strip(),
                "url": str(a.get("url") or "").strip(),
            }
        )

    ref_videos: list[dict[str, Any]] = []
    for v in transcripts[:6]:
        if not isinstance(v, dict):
            continue
        text = str(v.get("text") or "").strip()
        ref_videos.append(
            {
                "title": str(v.get("title") or "").strip(),
                "url": str(v.get("url") or "").strip(),
                "text_excerpt": text[:800] + ("…" if len(text) > 800 else ""),
                "used": str(v.get("used") or "").strip(),
                "error": str(v.get("error") or "").strip(),
            }
        )

    # materials 目前主要承载"视频文本"，但也可能包含其他；这里仅做简要采样。
    ref_materials: list[dict[str, Any]] = []
    for m in (materials or [])[:10]:
        if not isinstance(m, dict):
            continue
        ref_materials.append(
            {
                "kind": m.get("kind"),
                "title": m.get("title"),
                "url": m.get("url"),
                "content_excerpt": _compact_llm_text(m.get("content") or "", limit=600),
                "source": m.get("source"),
            }
        )

    def _build_messages() -> list[dict[str, str]]:
        system = {
            "role": "system",
            "content": (
                "你是短视频/中视频的口播文案编辑。"
                "输出必须只包含最终口播正文，禁止输出任何思维链、推理过程、分析过程或 `<think>` 标签。"
            ),
        }

        prompt = {
            "role": "user",
            "content": (
                "请你生成一篇用于抖音/视频号的“内容型口播文案”（法律/法治向为主）。\n"
                "目标：前 3 秒留人、信息密度高、适度融入网络热梗但不油腻。\n\n"
                "重要：禁止输出任何思维链/推理过程；不要出现 `<think>` / `</think>` 等标签。\n\n"
                "输出规范（非常重要）：\n"
                "- 直接从正文第一句开始输出，不要先写标题/摘要/要点/自检/字数统计\n"
                "- 禁止输出英文解释、字符计数过程、分段编号（例如“Paragraph 1/2/3”）\n\n"
                f"标题：《{proposal.get('title') or ''}》\n"
                f"核心论点：{proposal.get('thesis') or ''}\n"
                "这个文案需要围绕上述标题展开，并以本次选择的热点为主线延展。\n"
                "热点事情经过（来自文章聚合总结，尽量以此为事实依据）：\n"
                f"{_compact_llm_text(article_analysis or '', limit=12000)}\n\n"
                "写作要求：\n"
                "1) 只输出口播文案正文：不要表格/分栏，不要 JSON，不要 Markdown 代码块\n"
                "2) 字数：严格控制在 820-900 字（尽量贴近 850）。如果初稿偏短，请自行补充“例子/类比/关键机制解释/一个反常识点”把字数补足；如果偏长请自行压缩。\n"
                "3) 必须遵循四层结构（用自然段体现即可，不要写成标题列表）：\n"
                "   - 黄金 3 秒钩子：痛点反问/颠覆认知/冲突点，直接点题\n"
                "   - 知识降维（约 15 秒核心内容）：把硬核点讲清楚、讲有趣，给明确结论\n"
                "   - 节奏设计：全稿尽量每 7 秒一个亮点（短句+强信息点/类比/小反转/数字化表达），避免一段到底\n"
                "   - 结尾引导：促成点赞/评论/转发/收藏（给一个具体可评论的问题）\n"
                "   - 段落节奏硬约束：至少 8 个自然段，尽量 9-11 段；每段 1-2 句为主，避免超长段落\n"
                "4) 平台适配：抖音偏快节奏高完播；视频号更依赖可信与可复述的“观点 + 解释”，语气稳但不拖沓\n"
                "5) 风格：法律/法治类、内容型为主；可适度结合热梗（最多 1-2 处、点到为止），不要低俗、不要攻击个人/群体；基调积极正面、展现中国视角\n"
                "   - 事实串联硬约束：必须在前 2-3 段内用一句话明确写出“发生了什么（A）/谁回应（B）/争议点（C）”，优先直接复用上面的热点事实描述\n"
                "6) 健康科普合规（仅当主题涉及健康/医学时必须遵守）：\n"
                "   - 不做诊断、不替代就医；不夸大疗效、不做广告式推荐\n"
                "   - 提到研究/数据要表述为“目前证据/可能/相关性”，避免绝对化结论\n"
                "   - 给安全提示：出现严重症状请及时就医；个体差异存在\n"
                "7) 事实边界：若资料不足或存在不确定点，明确写“尚待核实/信息不完整”，不要编造\n"
                "8) 自检：写完后在心里粗估字数接近 850，并确保前 3 秒钩子足够强；不要输出任何自检过程\n"
            ),
        }
        return [system, prompt]

    messages = _build_messages()
    def _len_for_check(s: str) -> int:
        # 与 cli.py 的统计口径保持一致：对最终字符串 strip 后计数（包含换行）。
        return len((s or "").strip())

    def _clean_final_script_output(text: str) -> str:
        """
        清理常见不合规尾巴（例如模型把"自检计数/英文解释"拼到脚本后面）。
        不生成新内容，只做截断与去包裹。
        """

        t = (text or "").strip()
        if not t:
            return t

        # 防止残留 think 标签污染最终文案
        t = re.sub(r"(?is)<\s*/\s*think\s*>", "", t).strip()

        # 去掉常见引号包裹（模型偶尔会把整段脚本用引号括起来）
        if len(t) >= 2 and (
            (t[0] == t[-1] and t[0] in ('"', "'")) or (t[0] == "“" and t[-1] == "”")
        ):
            t = t[1:-1].strip()

        # 尝试截断"自检/计数"尾巴：只截断在明显英文开头的段落处，避免误伤正文。
        # 典型："\nNow count characters" / "\nI'll break it" / "Line 1:"
        markers = (
            "\nNow ",
            "\nNow count",
            "\nI'll ",
            "\nI will ",
            "\nLine ",
            "\nCount ",
            # 中文常见的自检/统计尾巴（尽量用换行开头，避免误伤正文）
            "\n字数",
            "\n字符数",
            "\n统计",
            "\n自检",
            "\n下面统计",
            "\n先统计",
        )
        cut_idx = -1
        for m in markers:
            idx = t.find(m)
            if idx != -1:
                cut_idx = idx
                break
        if cut_idx != -1:
            before = t[:cut_idx].rstrip()
            if before:
                debug_log("final_script cleaned trailing self-check", cfg=cfg, prefix="node")
                t = before

        # 兼容：模型输出 Markdown 代码块（不应出现），若出现则去掉 fence 行
        if "```" in t:
            lines = [ln for ln in t.splitlines() if ln.strip() != "```"]
            t2 = "\n".join(lines).strip()
            if t2:
                t = t2

        return t.strip()

    # 口播正文：在提示词中约束字数即可；此处不做"字数检查/报错"，避免因轻微超出导致流程中断。
    # 仍保持 minimax_reasoning_split=0，避免部分模型 content 为空导致拿不到正文。
    local_cfg = {
        **cfg,
        "minimax_reasoning_split": "0",
        "minimax_temperature": 0.2,
    }
    text = _strip_think_blocks(_llm_chat(messages, cfg=local_cfg))
    out = _clean_final_script_output(text or "")
    out_len = _len_for_check(out)
    debug_log(f"final_script len={out_len}", cfg=cfg, prefix="node")
    if not out.strip():
        raise RuntimeError("final_script 口播文案为空：LLM 输出为空")
    return out


def llm_get_final_script_prompt_text(
    *,
    proposal: TopicProposal,
    materials: list[Material],
    article_analysis: str,
    video_analysis: str,
    article_extracts: list[dict[str, Any]] | None = None,
    video_transcripts: list[dict[str, Any]] | None = None,
) -> dict[str, str]:
    """
    返回 final_script 节点"传给大模型的提示词文本"（system + user 的 content）。

    - 只做拼装，不做任何网络调用
    - 返回内容不包含占位符/代号；即为模型最终接收到的文字
    """

    extracts = list(article_extracts or [])
    transcripts = list(video_transcripts or [])

    ref_articles: list[dict[str, Any]] = []
    for a in extracts[:8]:
        if not isinstance(a, dict):
            continue
        ref_articles.append(
            {
                "title": str(a.get("title") or "").strip(),
                "url": str(a.get("url") or "").strip(),
            }
        )

    ref_videos: list[dict[str, Any]] = []
    for v in transcripts[:6]:
        if not isinstance(v, dict):
            continue
        text = str(v.get("text") or "").strip()
        ref_videos.append(
            {
                "title": str(v.get("title") or "").strip(),
                "url": str(v.get("url") or "").strip(),
                "text_excerpt": text[:800] + ("…" if len(text) > 800 else ""),
                "used": str(v.get("used") or "").strip(),
                "error": str(v.get("error") or "").strip(),
            }
        )

    ref_materials: list[dict[str, Any]] = []
    for m in (materials or [])[:10]:
        if not isinstance(m, dict):
            continue
        ref_materials.append(
            {
                "kind": m.get("kind"),
                "title": m.get("title"),
                "url": m.get("url"),
                "content_excerpt": _compact_llm_text(m.get("content") or "", limit=600),
                "source": m.get("source"),
            }
        )

    system_content = (
        "你是短视频/中视频的口播文案编辑。"
        "输出必须只包含最终口播正文，禁止输出任何思维链、推理过程、分析过程或 `<think>` 标签。"
    )

    user_content = (
        "请你生成一篇用于抖音/视频号的“内容型口播文案”（法律/法治向为主）。\n"
        "目标：前 3 秒留人、信息密度高、适度融入网络热梗但不油腻。\n\n"
        "重要：禁止输出任何思维链/推理过程；不要出现 `<think>` / `</think>` 等标签。\n\n"
        "输出规范（非常重要）：\n"
        "- 直接从正文第一句开始输出，不要先写标题/摘要/要点/自检/字数统计\n"
        "- 禁止输出英文解释、字符计数过程、分段编号（例如“Paragraph 1/2/3”）\n\n"
        f"标题：《{proposal.get('title') or ''}》\n"
        f"核心论点：{proposal.get('thesis') or ''}\n"
        "这个文案需要围绕上述标题展开，并以本次选择的热点为主线延展。\n"
        "热点事情经过（来自文章聚合总结，尽量以此为事实依据）：\n"
        f"{_compact_llm_text(article_analysis or '', limit=12000)}\n\n"
        "写作要求：\n"
        "1) 只输出口播文案正文：不要表格/分栏，不要 JSON，不要 Markdown 代码块\n"
        "2) 字数：严格控制在 820-900 字（尽量贴近 850）。如果初稿偏短，请自行补充“例子/类比/关键机制解释/一个反常识点”把字数补足；如果偏长请自行压缩。\n"
        "3) 必须遵循四层结构（用自然段体现即可，不要写成标题列表）：\n"
        "   - 黄金 3 秒钩子：痛点反问/颠覆认知/冲突点，直接点题\n"
        "   - 知识降维（约 15 秒核心内容）：把硬核点讲清楚、讲有趣，给明确结论\n"
        "   - 节奏设计：全稿尽量每 7 秒一个亮点（短句+强信息点/类比/小反转/数字化表达），避免一段到底\n"
        "   - 结尾引导：促成点赞/评论/转发/收藏（给一个具体可评论的问题）\n"
        "   - 段落节奏硬约束：至少 8 个自然段，尽量 9-11 段；每段 1-2 句为主，避免超长段落\n"
        "4) 平台适配：抖音偏快节奏高完播；视频号更依赖可信与可复述的“观点 + 解释”，语气稳但不拖沓\n"
        "5) 风格：法律/法治类、内容型为主；可适度结合热梗（最多 1-2 处、点到为止），不要低俗、不要攻击个人/群体；基调积极正面、展现中国视角\n"
        "   - 事实串联硬约束：必须在前 2-3 段内用一句话明确写出“发生了什么（A）/谁回应（B）/争议点（C）”，优先直接复用上面的热点事实描述\n"
        "6) 健康科普合规（仅当主题涉及健康/医学时必须遵守）：\n"
        "   - 不做诊断、不替代就医；不夸大疗效、不做广告式推荐\n"
        "   - 提到研究/数据要表述为“目前证据/可能/相关性”，避免绝对化结论\n"
        "   - 给安全提示：出现严重症状请及时就医；个体差异存在\n"
        "7) 事实边界：若资料不足或存在不确定点，明确写“尚待核实/信息不完整”，不要编造\n"
        "8) 自检：写完后在心里粗估字数接近 850，并确保前 3 秒钩子足够强；不要输出任何自检过程\n"
    )

    return {"system_prompt": system_content, "user_prompt": user_content}


def llm_split_narration_to_segments(
    *,
    title: str,
    narration_text: str,
    cfg: dict[str, Any],
) -> list[dict[str, Any]]:
    """
    把一段口播文案切成"可分镜"的小片段（只输出 spoken_text，不加画面提示）。

    返回 list[{"spoken_text": "..."}]。
    约束：必须是严格 JSON；不合规则抛错（允许做格式修复型重试）。
    """

    def _validate(obj: Any) -> list[dict[str, Any]] | None:
        if isinstance(obj, dict) and isinstance(obj.get("segments"), list):
            segs = obj.get("segments")
        elif isinstance(obj, list):
            segs = obj
        else:
            return None

        out: list[dict[str, Any]] = []
        for x in segs:
            if not isinstance(x, dict):
                return None
            spoken = str(x.get("spoken_text") or x.get("text") or "").strip()
            if not spoken:
                return None
            out.append({"spoken_text": spoken})
        if not out:
            return None
        return out

    base_content = (
        "请把下面的口播文案切分成多个“分镜片段”，用于后续逐段添加画面提示。\n"
        "只做切分，不要改写内容、不加总结、不加画面提示。\n"
        "输出必须是严格 JSON，只能输出 JSON，不要任何额外文字；禁止输出分析过程或 <think>。\n\n"
        "JSON 格式：\n"
        "{\"segments\":[{\"spoken_text\":\"...\"},{\"spoken_text\":\"...\"}]}\n\n"
        "切分要求：\n"
        "- 每个片段尽量 1-3 句，语义完整\n"
        "- 片段长度尽量均匀，避免过长（>300字）或过短（<20字）\n"
        "- 保留原有时间顺序\n\n"
        f"视频标题：{title}\n\n"
        f"口播文案：\n{narration_text}\n"
    )
    prompt = {"role": "user", "content": base_content}
    system_prompt = {
        "role": "system",
        "content": (
            "你是一个只输出 JSON 的接口。"
            "无论如何都不要输出解释、推理、过程、<think>、markdown code fence。"
            "只返回一个 JSON 对象。"
        ),
    }

    # 结构化任务：强制 JSON object 输出，显著降低"解释/思维链"概率。
    # max_tokens 由全局配置控制（LLM_MAX_TOKENS=0 表示不限制，尽量用模型上限）。
    local_cfg = {
        **cfg,
        "minimax_temperature": "0",
        "llm_response_format": {"type": "json_object"},
        "minimax_reasoning_split": True,
    }
    last_text = ""
    for attempt in range(2):
        text = _llm_chat([system_prompt, prompt], cfg=local_cfg) or ""
        last_text = text
        cleaned = _strip_think_blocks(text)
        obj = _extract_json_object(cleaned) or _extract_json_object(last_text) or None
        parsed: Any = obj
        if parsed is None:
            arr = _extract_json_array(cleaned) or _extract_json_array(last_text)
            parsed = arr
        segs = _validate(parsed)
        if segs:
            return segs

        # 修复型重试：不要把"上次的长解释/思维链"塞回去（会继续放大 token 并诱发 length 截断）。
        # 若上次输出完全不可用（例如只剩 <think>），则直接强制重新按原始输入输出 JSON。
        prompt = {
            "role": "user",
            "content": (
                "直接输出严格 JSON，只能输出 JSON，不要任何额外文字，也不要输出 <think> 或解释。\n"
                "格式必须是：{\"segments\":[{\"spoken_text\":\"...\"}]}\n"
                "约束：segments 必须是非空数组；每项必须有 spoken_text。\n\n"
                + base_content
            ),
        }

    snippet = (last_text or "").strip().replace("\n", " ")[:280]
    raise RuntimeError(f"LLM 分镜切段输出不合规（期望 segments JSON）：{snippet!r}")


def llm_add_visual_prompts(
    *,
    title: str,
    segments: list[dict[str, Any]],
    cfg: dict[str, Any],
) -> list[dict[str, Any]]:
    """
    为每个 spoken_text 片段补充 visual_prompt（画面提示）。

    返回 list[{"spoken_text": "...", "visual_prompt": "..."}]，数量必须与输入一致。
    """

    spoken_list = [str(s.get("spoken_text") or "").strip() for s in (segments or [])]
    if not spoken_list or any(not s for s in spoken_list):
        raise RuntimeError("llm_add_visual_prompts 输入 segments 为空或包含空 spoken_text")

    def _validate(obj: Any) -> list[dict[str, Any]] | None:
        if isinstance(obj, dict) and isinstance(obj.get("segments"), list):
            xs = obj.get("segments")
        elif isinstance(obj, list):
            xs = obj
        else:
            return None
        if not isinstance(xs, list) or len(xs) != len(spoken_list):
            return None

        out: list[dict[str, Any]] = []
        for i, x in enumerate(xs):
            if not isinstance(x, dict):
                return None
            spoken = str(x.get("spoken_text") or x.get("text") or "").strip()
            visual = str(x.get("visual_prompt") or x.get("visual") or "").strip()
            if not visual:
                return None
            # spoken_text 必须保持一致，避免"偷改文案"导致对不上
            if spoken and spoken != spoken_list[i]:
                return None
            out.append({"spoken_text": spoken_list[i], "visual_prompt": visual})
        return out

    base_content = (
        "你是视频分镜导演，请为每个口播片段补充“画面提示”。\n"
        "要求：\n"
        "- 不要改写口播内容（spoken_text 必须原样保留）\n"
        "- 每个 visual_prompt 用中文，描述镜头/画面主体/动作/氛围/可用素材类型（实拍/动效/截图/字幕/图表）\n"
        "- 每条 visual_prompt 尽量控制在 60-120 个中文字符（避免过长导致 JSON 被截断）\n"
        "- 避免侵权与不实细节；不确定就用抽象/通用画面\n"
        "- 输出必须是严格 JSON，只能输出 JSON\n\n"
        "JSON 格式：\n"
        "{\"segments\":[{\"spoken_text\":\"...\",\"visual_prompt\":\"...\"}]}\n\n"
        f"视频标题：{title}\n\n"
        "口播片段列表：\n"
        + json.dumps([{"spoken_text": s} for s in spoken_list], ensure_ascii=False)
    )
    prompt = {"role": "user", "content": base_content}

    system_prompt = {
        "role": "system",
        "content": (
            "你是一个只输出 JSON 的接口。"
            "无论如何都不要输出解释、推理、过程、<think>、markdown code fence。"
            "只返回一个 JSON 对象。"
        ),
    }

    # 结构化任务：强制 JSON object 输出，降低"解释/思维链"概率。
    # max_tokens 由全局配置控制（LLM_MAX_TOKENS=0 表示不限制，尽量用模型上限）。
    local_cfg = {
        **cfg,
        "minimax_temperature": "0",
        "llm_response_format": {"type": "json_object"},
        "minimax_reasoning_split": True,
    }
    last_text = ""
    for _ in range(2):
        text = _llm_chat([system_prompt, prompt], cfg=local_cfg) or ""
        last_text = text
        cleaned = _strip_think_blocks(text)
        obj = _extract_json_object(cleaned) or _extract_json_object(last_text) or None
        parsed: Any = obj
        if parsed is None:
            arr = _extract_json_array(cleaned) or _extract_json_array(last_text)
            parsed = arr
        out = _validate(parsed)
        if out:
            return out

        snippet = (cleaned or last_text or "").strip().replace("\n", " ")[:1200]
        repair_prompt = {
            "role": "user",
            "content": (
                "把你的输出整理成严格 JSON，只输出如下格式：\n"
                "{\"segments\":[{\"spoken_text\":\"...\",\"visual_prompt\":\"...\"}]}\n"
                "约束：只能输出 JSON；segments 数量必须与输入一致；spoken_text 必须与输入逐条一致。\n\n"
                f"原始输出：{snippet}\n\n"
                + base_content
            ),
        }
        prompt = repair_prompt

    snippet = (last_text or "").strip().replace("\n", " ")[:280]
    raise RuntimeError(f"LLM 画面提示补全输出不合规（期望 segments JSON）：{snippet!r}")


def llm_summarize_hot_event(
    *,
    hot_titles: list[str],
    article_extracts: list[dict[str, Any]],
    cfg: dict[str, Any],
) -> str:
    sample_docs: list[str] = []
    for idx, item in enumerate(article_extracts[:5], start=1):
        title = str(item.get("title") or "").strip()
        url = str(item.get("url") or "").strip()
        content = _compact_llm_text(item.get("content") or "", limit=2500)
        sample_docs.append(f"【{idx}】{title}\nURL: {url}\n{content}")

    prompt = {
        "role": "user",
        "content": (
            "你是一个严谨的热点事件编辑。\n"
            "请基于下面 5 篇文章的主要内容，整理出这个热点的“详细事件总结”。\n"
            "要求：\n"
            "1) 只保留可验证事实，不要脑补\n"
            "2) 重点讲清：事件背景、发生了什么、关键时间线、核心争议/疑问、对普通人意味着什么\n"
            "3) 如果多篇文章有重复信息，请合并，不要重复写\n"
            "4) 输出要适合后续选题策划使用，信息密度高\n"
            "5) 直接输出正文，不要 JSON，不要 Markdown 代码块\n\n"
            f"热点标题：{json.dumps(hot_titles, ensure_ascii=False)}\n\n"
            f"文章内容：\n\n{'\n\n---\n\n'.join(sample_docs)}"
        ),
    }
    return _llm_chat([prompt], cfg={**cfg, "minimax_temperature": "0"}).strip()


def llm_summarize_hot_event_from_html(
    *,
    hot_titles: list[str],
    article_pages: list[dict[str, Any]],
    cfg: dict[str, Any],
) -> str:
    sample_docs: list[str] = []
    for idx, item in enumerate(article_pages[:5], start=1):
        url = str(item.get("url") or "").strip()
        title = str(item.get("title") or "").strip()
        html = _compact_llm_text(item.get("html") or "", limit=16000)
        sample_docs.append(f"【{idx}】{title}\nURL: {url}\nHTML:\n{html}")

    prompt = {
        "role": "user",
        "content": (
            "你是一个严谨的热点事件编辑。\n"
            f"下面是 {len(sample_docs)} 个网页的原始 HTML（可能包含导航栏、广告、脚本等噪声）。\n"
            "请从中抽取并整理这个热点的“详细事件总结”。\n"
            "要求：\n"
            "1) 只保留可验证事实，不要脑补；不确定就明确写“不确定/需核验”\n"
            "2) 重点讲清：事件背景、发生了什么、关键时间线、核心争议/疑问、对普通人意味着什么\n"
            "3) 多篇网页重复的信息请合并，不要重复写\n"
            "4) 尽量标明信息来自哪篇网页（用 URL 作为引用标记即可）\n"
            "5) 直接输出正文，不要 JSON，不要 Markdown 代码块\n\n"
            f"热点标题：{json.dumps(hot_titles, ensure_ascii=False)}\n\n"
            f"网页 HTML：\n\n{'\n\n---\n\n'.join(sample_docs)}"
        ),
    }
    return _llm_chat([prompt], cfg={**cfg, "minimax_temperature": "0"}).strip()


def llm_analyze_materials(
    *,
    kind: str,
    hot_titles: list[str],
    materials: list[Material],
    cfg: dict[str, Any],
) -> str:
    """
    对某一类素材做"信息提炼"。
    - kind=article：更关注事实、数据、原理、影响
    - kind=video：更关注大众关注点、争议点、传播点
    """

    sample = []
    for m in materials[:5]:
        sample.append(
            {
                "title": m.get("title"),
                "url": m.get("url"),
                "snippet": m.get("snippet"),
                "content": (m.get("content") or "")[:1200],
            }
        )

    prompt = {
        "role": "user",
        "content": (
            "你是一个严谨的法律/法治内容编辑。下面是我们抓到的素材（同一热点相关）。\n"
            f"素材类型：{kind}\n"
            f"热点标题：{hot_titles}\n"
            "请输出：\n"
            "1) 事实要点（可核验）\n"
            "2) 关键概念/术语解释\n"
            "3) 可能的争议点与误解\n"
            "4) 对普通人的影响（边界条件）\n"
            "要求：用中文，条理清晰。\n\n"
            f"素材样本(JSON)：{json.dumps(sample, ensure_ascii=False)}"
        ),
    }
    return _llm_chat([prompt], cfg=cfg)


def llm_filter_videos(
    *,
    keyword: str,
    videos: list[dict[str, Any]],
    cfg: dict[str, Any],
    keep_limit: int = 20,
) -> list[dict[str, Any]]:
    """
    从一批候选视频中筛选出"对该词条的研究与扩展更有收益"的视频。

    返回值：原始 video dict 的子集（会尽量保留顺序）。
    """

    # 只把必要字段发给 LLM，避免 token 爆炸
    items: list[dict[str, Any]] = []
    max_in = min(int(cfg.get("video_llm_input_limit") or 60), 24)
    for v in videos[: max(1, max_in)]:
        items.append(
            {
                "rank": v.get("rank"),
                "title": _compact_llm_text(v.get("title"), limit=80),
                "source": _compact_llm_text(v.get("source") or _result_source(v.get("url")), limit=32),
                "snippet": _compact_llm_text(v.get("snippet") or "", limit=120),
            }
        )

    system = {
        "role": "system",
        "content": (
            "你是一个只输出 JSON 的程序。严格遵守：\n"
            "1) 只输出 JSON，不要解释，不要 Markdown，不要代码块，不要 <think>。\n"
            "2) 输出必须是 JSON 对象，且必须包含 keep_ranks 字段（整数数组）。\n"
        ),
    }

    prompt = {
        "role": "user",
        "content": (
            "你是严谨的法律/法治内容研究员。我们正在为一个热门词条做资料扩展。\n"
            f"词条：{keyword}\n\n"
            "下面是 B 站搜索到的一批视频候选（rank 从 1 开始）。\n"
            "请筛选出最有“研究价值/可延展/信息密度高/可验证”的视频，过滤掉纯娱乐、标题党、无关内容。\n"
            f"要求：最多保留 {keep_limit} 条。\n"
            "keep_ranks 必须取自候选的 rank 字段（不要输出候选数组下标）。\n"
            "只输出 JSON，格式如下：\n"
            "{\"keep_ranks\":[1,5,9]}\n\n"
            f"候选(JSON)：{json.dumps(items, ensure_ascii=False)}"
        ),
    }

    debug_log(f"op=filter_videos keyword={keyword!r} candidates={len(items)} keep={keep_limit}", cfg=cfg)

    keep = _pick_keep_ranks_with_retry(
        op="filter_videos_keep_ranks",
        keyword=keyword,
        items=items,
        system=system,
        user_base=prompt,
        cfg=cfg,
        keep_limit=keep_limit,
        # 视频链路允许返回空数组（不应因为空选择导致全链路失败）
        require_non_empty=False,
        min_required=0,
        max_attempts=3,
        repair_reasoning_split="1",
    )
    keep_set = set(keep)

    out: list[dict[str, Any]] = []
    for v in videos:
        r = v.get("rank")
        if isinstance(r, int) and r in keep_set:
            out.append(v)
            if len(out) >= keep_limit:
                break
    return out


def llm_filter_articles(
    *,
    keyword: str,
    articles: list[dict[str, Any]],
    cfg: dict[str, Any],
    keep_limit: int = 5,
) -> list[dict[str, Any]]:
    """
    从一批候选文章中筛选出"对该词条的研究与扩展更有收益"的文章。
    """

    items: list[dict[str, Any]] = []
    max_in = min(int(cfg.get("article_llm_input_limit") or 50), 12)
    # 按搜索结果原始顺序喂给 LLM：不做来源/站点过滤，不强依赖 source 字段
    for article in (articles or [])[: max(1, max_in)]:
        url = str(article.get("url") or "").strip()
        items.append(
            {
                "rank": article.get("rank"),
                "title": _compact_llm_text(article.get("title"), limit=80),
                "url": url,
            }
        )

    system = {
        "role": "system",
        "content": (
            "你是一个只输出 JSON 的程序。严格遵守：\n"
            "1) 只输出 JSON，不要解释，不要 Markdown，不要代码块，不要 <think>。\n"
            "2) 输出必须是 JSON 对象，且必须包含 keep_ranks 字段（整数数组）。\n"
        ),
    }

    prompt = {
        "role": "user",
        "content": (
            f"词条：{keyword}\n"
            "任务：根据标题（必要时参考 URL）筛选出最值得继续参考的文章。\n"
            "保留标准：相关、信息密度高、角度不重复。\n"
            "优先保留：发布信息、技术解读、产业影响、争议澄清、权威媒体或专业站点。\n"
            "排除：视频站、短视频站、下载站、明显问答站、营销标题、传闻、社媒口水文、明显重复、弱相关文章。\n"
            f"要求：如果候选足够，尽量返回 {keep_limit} 条；如果候选≥3，请至少返回 3 条；如果明显相关的不够，再少于 {keep_limit} 也可以。\n"
            "keep_ranks 必须取自候选的 rank 字段（不要输出候选数组下标）。\n"
            "只输出：{\"keep_ranks\":[...]}。\n\n"
            f"候选(JSON)：{json.dumps(items, ensure_ascii=False)}"
        ),
    }

    debug_log(f"op=filter_articles keyword={keyword!r} candidates={len(items)} keep={keep_limit}", cfg=cfg)

    keep = _pick_keep_ranks_with_retry(
        op="filter_articles_keep_ranks",
        keyword=keyword,
        items=items,
        system=system,
        user_base=prompt,
        cfg=cfg,
        keep_limit=keep_limit,
        # 文章链路是前置关键节点：空选择会导致下游无法继续
        require_non_empty=True,
        min_required=min(3, int(keep_limit or 0)),
        max_attempts=3,
        repair_reasoning_split="0",
    )
    keep_set = set(keep)

    out: list[dict[str, Any]] = []
    for article in articles:
        rank = article.get("rank")
        if isinstance(rank, int) and rank in keep_set:
            out.append(article)
            if len(out) >= keep_limit:
                break
    return out


def _is_article_source(article: dict[str, Any]) -> bool:
    host = _result_source(article.get("url") or article.get("source") or "")
    blocked = (
        "bilibili.com",
        "youtube.com",
        "youtu.be",
        "douyin.com",
        "iesdouyin.com",
        "ixigua.com",
        "haokan.baidu.com",
        "kuaishou.com",
        "live.kuaishou.com",
        "xiaohongshu.com",
        "zhihu.com",
        "zhidao.baidu.com",
        "jingyan.baidu.com",
    )
    return not any(host == item or host.endswith("." + item) for item in blocked)


def llm_infer_account_topics(
    *,
    hot_titles: list[str],
    article_analysis: str,
    video_analysis: str,
    cfg: dict[str, Any],
) -> list[TopicProposal]:
    """
    基于"热点详细事件 + 账号风格"生成 10 套完整选题候选。
    """

    base_content = (
        "你是一个法律/法治方向的短视频选题策划。\n"
        "我们的账号主题：法律/法治（偏法规解读、案例分析、严谨、可验证）。\n"
        "下面给你的是当前热点的详细事件总结，请必须基于这个详细事件来发散选题，不能脱离事件乱想。\n"
        "请直接输出 10 套选题方案，不要只给方向词。每套包含：\n"
        "- candidate_id: \"T01\" 到 \"T10\"\n"
        "- title: 选题标题（尽量 18 个字以内）\n"
        "- thesis: 核心论点（1 句话，尽量 35 个字以内）\n"
        "要求：\n"
        "1) 每套都要明显不同，不要同义改写凑数\n"
        "2) 必须围绕观众对这个热点最关心的问题、误解、风险、影响、边界条件来设计\n"
        "3) 保持法律/法治账号调性，严谨、可验证、不八卦\n"
        "4) 要让用户有点击欲和期待感，但不能标题党\n"
        "5) 全部内容务必简洁，避免长句，避免重复背景铺垫\n"
        "输出要求：只输出 JSON 对象，格式为 {\"proposals\":[{\"candidate_id\":\"T01\",\"title\":\"...\",\"thesis\":\"...\"}, ...]}（不要解释、不要 Markdown、不要思考过程）。\n\n"
        f"热点标题：{json.dumps(hot_titles, ensure_ascii=False)}\n"
        f"热点详细事件：{(article_analysis or '')[:3000]}\n"
        f"视频素材提炼(截断)：{(video_analysis or '')[:2000]}\n"
    )

    system = {
        "role": "system",
        "content": (
            "你是一个只输出 JSON 的程序。严格遵守：\n"
            "1) 只输出 JSON，不要解释，不要 Markdown，不要 <think>，不要思考过程。\n"
            "2) 输出必须是 JSON 对象，且必须包含 proposals 字段（数组）。\n"
        ),
    }

    def _validate_proposals(xs: Any) -> list[TopicProposal]:
        if not isinstance(xs, list):
            return []
        out: list[TopicProposal] = []
        seen: set[str] = set()
        for it in xs:
            if not isinstance(it, dict):
                continue
            candidate_id = str(it.get("candidate_id") or "").strip().upper()
            title = str(it.get("title") or "").strip()
            thesis = str(it.get("thesis") or "").strip()
            if not candidate_id or not title or not thesis:
                continue
            if candidate_id in seen:
                continue
            if "..." in title or title in ("...", "…"):
                continue
            out.append(
                {
                    "proposal_id": candidate_id,
                    "candidate_id": candidate_id,
                    "title": title,
                    "thesis": thesis,
                }
            )
            seen.add(candidate_id)
        return out

    last_text = ""
    for attempt in range(1, 4):
        local_cfg: dict[str, Any] = {
            **cfg,
            "minimax_temperature": "0",
            "llm_response_format": "json_object",
            "minimax_reasoning_split": "0",
        }
        prompt = {
            "role": "user",
            "content": base_content if attempt == 1 else ("上次输出不合规，请严格只输出 JSON。\n" + base_content),
        }
        text = _llm_chat([system, prompt], cfg=local_cfg)
        last_text = text or ""
        obj = _extract_json_object(last_text)
        items = obj.get("proposals") if isinstance(obj, dict) else None
        parsed = _validate_proposals(items)
        if len(parsed) == 10:
            return parsed

        # 修复：把"非 JSON / 含 <think> 的内容"整理成严格 JSON
        repair_prompt = {
            "role": "user",
            "content": (
                "把下面内容整理成严格 JSON 对象，格式必须是：\n"
                "{\"proposals\":[{\"candidate_id\":\"T01\",\"title\":\"...\",\"thesis\":\"...\"}, ...]}\n"
                "只输出 JSON，不要任何解释。\n\n"
                + base_content
                + "\n原始内容："
                + last_text[:4000]
            ),
        }
        repaired = _llm_chat(
            [system, repair_prompt],
            cfg={**local_cfg, "minimax_reasoning_split": "0"},
        )
        repaired_obj = _extract_json_object(repaired or "")
        repaired_items = repaired_obj.get("proposals") if isinstance(repaired_obj, dict) else None
        parsed2 = _validate_proposals(repaired_items)
        if len(parsed2) == 10:
            return parsed2

    snippet = (last_text or "").strip().replace("\n", " ")[:280]
    raise RuntimeError(f"LLM 账号选题推理输出不合规（期望 10 条 proposals JSON）：{snippet!r}")


def llm_infer_account_topics_and_generate_proposals(
    *,
    hot_titles: list[str],
    article_analysis: str,
    video_analysis: str,
    materials: list[Material],
    cfg: dict[str, Any],
) -> tuple[list[TopicProposal], list[TopicProposal]]:
    return _llm_infer_and_generate(
        hot_titles=hot_titles,
        article_analysis=article_analysis,
        video_analysis=video_analysis,
        materials=materials,
        cfg=cfg,
        llm_chat=_llm_chat,
        extract_json_object=_extract_json_object,
    )


def _openai_compat_chat(messages: list[dict[str, Any]], *, cfg: dict[str, Any]) -> str:
    provider = (cfg.get("llm_provider") or "").lower().strip()
    is_deepseek = provider == "deepseek"

    if is_deepseek:
        api_key = cfg.get("deepseek_api_key") or ""
        base_url = cfg.get("deepseek_base_url") or "https://api.deepseek.com"
        model = cfg.get("deepseek_model") or "deepseek-v4-pro"
        temperature = float(cfg.get("deepseek_temperature") or 0.2)
        timeout_seconds = float(cfg.get("deepseek_timeout_seconds") or 300)
    else:
        api_key = cfg.get("openai_api_key") or ""
        base_url = cfg.get("openai_base_url") or "https://api.openai.com/v1"
        model = cfg.get("openai_model") or "gpt-4.1-mini"
        temperature = float(cfg.get("openai_temperature") or 0.4)
        timeout_seconds = float(cfg.get("openai_timeout_seconds") or 0)

    max_tokens = int(cfg.get("llm_max_tokens") or 0)

    if not api_key:
        raise RuntimeError(f"当 LLM_PROVIDER={provider} 时，需要设置对应的 API_KEY")

    if not is_deepseek:
        if "minimaxi.com" in base_url or str(model).startswith("minimax/"):
            model = _normalize_minimax_model(str(model))
        if "minimaxi.com" in base_url:
            timeout_seconds = float(cfg.get("minimax_timeout_seconds") or timeout_seconds or 300)

    url = base_url.rstrip("/") + "/chat/completions"
    payload: dict[str, Any] = {"model": model, "messages": messages, "temperature": temperature}
    if max_tokens > 0:
        if is_deepseek:
            payload["max_tokens"] = max_tokens
        elif "minimaxi.com" in base_url or str(model).startswith("MiniMax-"):
            payload["max_completion_tokens"] = max_tokens
        else:
            payload["max_tokens"] = max_tokens
    # DeepSeek non-thinking mode: disable reasoning via thinking param
    if is_deepseek:
        payload["thinking"] = {"type": "disabled"}
    # 可选：结构化输出（OpenAI 兼容）
    rf = cfg.get("llm_response_format")
    if isinstance(rf, dict):
        payload["response_format"] = rf
    elif isinstance(rf, str) and rf.strip().lower() == "json_object":
        payload["response_format"] = {"type": "json_object"}
    headers = {"Authorization": f"Bearer {api_key}"}
    timeout = None if timeout_seconds <= 0 else timeout_seconds

    t0 = time.time()
    debug_log(
        f"openai_compat request provider={provider!r} url={url!r} model={model!r} temperature={temperature} "
        f"timeout={timeout} messages={len(messages)}",
        cfg=cfg,
    )
    _debug_log_json("openai_compat request_messages", messages, cfg=cfg)
    _debug_log_json("openai_compat request_payload", payload, cfg=cfg)
    with httpx.Client(timeout=timeout) as client:
        try:
            r = client.post(url, headers=headers, json=payload)
            r.raise_for_status()
            data = r.json()
        except BaseException as e:
            debug_log(f"openai_compat exception={type(e).__name__}: {e!r}", cfg=cfg)
            raise
        finally:
            debug_log(f"openai_compat elapsed={time.time()-t0:.2f}s", cfg=cfg)

    _debug_log_json("openai_compat raw_response", data, cfg=cfg)
    try:
        debug_log(f"openai_compat finish_reason={data['choices'][0].get('finish_reason')!r}", cfg=cfg)
    except Exception:
        pass

    try:
        content = data["choices"][0]["message"]["content"]
        if isinstance(content, str):
            cleaned = _strip_think_blocks(content)
            debug_log(f"openai_compat raw_content={content}", cfg=cfg)
            debug_log(f"openai_compat content={cleaned}", cfg=cfg)
            return cleaned
        return content
    except Exception as e:
        raise RuntimeError(f"Unexpected LLM response: {json.dumps(data)[:500]}") from e


def _minimax_chat(messages: list[dict[str, Any]], *, cfg: dict[str, Any]) -> str:
    """
    MiniMax OpenAI API 兼容调用。

    文档：OpenAI API 兼容（文本）
    - base_url: https://api.minimaxi.com/v1
    - path: /chat/completions
    - model: MiniMax-M2.7 / MiniMax-M2.7-highspeed 等
    """

    api_key = cfg.get("minimax_api_key") or ""
    base_url = cfg.get("minimax_base_url") or "https://api.minimaxi.com/v1"
    model = _normalize_minimax_model(cfg.get("minimax_model") or "") or "MiniMax-M2.7-highspeed"
    temperature = float(cfg.get("minimax_temperature") or 0.4)
    timeout_seconds = float(cfg.get("minimax_timeout_seconds") or 300)
    max_tokens = int(cfg.get("llm_max_tokens") or 0)
    # MiniMax OpenAI 兼容接口支持 reasoning_split（可选）。
    # 默认关闭，避免部分模型把"最终答案"塞进 reasoning_content 导致下游取不到。
    # 但对严格结构化任务，可通过 cfg 显式开启，往往能把"思维链"拆到 reasoning_content，
    # 让 content 更干净（更容易得到纯 JSON）。
    reasoning_split = _parse_truthy(cfg.get("minimax_reasoning_split"))

    if not api_key:
        raise RuntimeError("当 LLM_PROVIDER=minimax 时，需要设置 MINIMAX_API_KEY")

    url = base_url.rstrip("/") + "/chat/completions"
    payload: dict[str, Any] = {"model": model, "messages": messages, "temperature": temperature}
    if max_tokens > 0:
        payload["max_completion_tokens"] = max_tokens
    # MiniMax OpenAI 兼容接口支持 reasoning_split（可选）
    payload["reasoning_split"] = reasoning_split
    # MiniMax OpenAI 兼容：response_format（可选）。用于强制输出 JSON 对象，提升结构化任务稳定性。
    rf = cfg.get("llm_response_format")
    if isinstance(rf, dict):
        payload["response_format"] = rf
    elif isinstance(rf, str) and rf.strip().lower() == "json_object":
        payload["response_format"] = {"type": "json_object"}
    headers = {"Authorization": f"Bearer {api_key}"}

    timeout = None if timeout_seconds <= 0 else timeout_seconds
    rf_for_log = payload.get("response_format")
    rf_tag = ""
    if isinstance(rf_for_log, dict) and rf_for_log.get("type"):
        rf_tag = f" response_format={rf_for_log.get('type')!r}"
    t0 = time.time()
    debug_log(
        f"minimax request url={url!r} model={model!r} temperature={temperature} timeout={timeout} "
        f"reasoning_split={reasoning_split} messages={len(messages)}{rf_tag}",
        cfg=cfg,
    )
    _debug_log_json("minimax request_messages", messages, cfg=cfg)
    _debug_log_json("minimax request_payload", payload, cfg=cfg)
    with httpx.Client(timeout=timeout) as client:
        try:
            r = client.post(url, headers=headers, json=payload)
            debug_log(f"minimax status_code={r.status_code}", cfg=cfg)
            r.raise_for_status()
            data = r.json()
        except httpx.HTTPStatusError as e:
            resp = e.response
            debug_log(
                f"minimax http_error status={resp.status_code} body_snippet={resp.text[:400]!r}",
                cfg=cfg,
            )
            raise
        except BaseException as e:
            debug_log(f"minimax exception={type(e).__name__}: {e!r}", cfg=cfg)
            raise
        finally:
            debug_log(f"minimax elapsed={time.time()-t0:.2f}s", cfg=cfg)

    _debug_log_json("minimax raw_response", data, cfg=cfg)
    try:
        debug_log(f"minimax finish_reason={data['choices'][0].get('finish_reason')!r}", cfg=cfg)
    except Exception:
        pass

    # 兼容部分非 OpenAI 结构的错误返回
    if isinstance(data, dict) and isinstance(data.get("base_resp"), dict):
        code = int(data["base_resp"].get("status_code") or 0)
        msg = str(data["base_resp"].get("status_msg") or "")
        if code != 0:
            raise RuntimeError(f"MiniMax 调用失败: status_code={code}, status_msg={msg!r}")

    try:
        msg = data["choices"][0]["message"]
        content = msg.get("content")
        if isinstance(content, str) and content.strip():
            raw_content = content.strip()
            content = _strip_think_blocks(content)
            # 严禁把思维链/推理过程当作最终产物返回（避免"假数据/假流程"式的污染输出）。
            if str(content).strip().lower().startswith("<think>"):
                raise RuntimeError("LLM 返回了未闭合的 <think> 思维链内容，无法作为有效输出")
            try:
                debug_log(f"minimax raw_content={raw_content}", cfg=cfg)
                debug_log(f"minimax content={content.strip()}", cfg=cfg)
            except Exception:
                pass
            return content
        # 兼容：部分 OpenAI-compat 实现会把 response_format 的内容直接放成 dict/list
        if isinstance(content, (dict, list)):
            try:
                s = json.dumps(content, ensure_ascii=False)
                try:
                    debug_log(f"minimax content_json={s}", cfg=cfg)
                except Exception:
                    pass
                return s
            except Exception:
                return str(content)
        # content 为空时，打点并做一个温和兜底：如果模型把结果放在 reasoning_details 里（极少见），尝试提取
        try:
            debug_log(
                "minimax empty content: "
                f"message_keys={list(msg.keys())[:20]} "
                f"reasoning_content_len={len(str(msg.get('reasoning_content') or ''))}",
                cfg=cfg,
            )
        except Exception:
            pass
        rc = msg.get("reasoning_content")
        if isinstance(rc, str) and rc.strip():
            # 有些情况下模型会把最终 JSON 放在 reasoning_content（尤其当 content 为空时）
            raw_rc = rc.strip()
            cleaned_rc = _strip_think_blocks(rc)
            try:
                debug_log(f"minimax raw_reasoning_content={raw_rc}", cfg=cfg)
                debug_log(f"minimax reasoning_content={cleaned_rc}", cfg=cfg)
            except Exception:
                pass
            return cleaned_rc
        details = msg.get("reasoning_details") or []
        if isinstance(details, list) and details:
            first = details[0]
            if isinstance(first, dict) and isinstance(first.get("text"), str):
                raw_text = first["text"]
                cleaned_text = _strip_think_blocks(raw_text)
                try:
                    debug_log(f"minimax raw_reasoning_details={str(raw_text).strip()}", cfg=cfg)
                    debug_log(f"minimax reasoning_details={cleaned_text}", cfg=cfg)
                except Exception:
                    pass
                return cleaned_text
        return ""
    except Exception as e:
        raise RuntimeError(f"MiniMax 响应结构异常: {json.dumps(data)[:500]}") from e
