from __future__ import annotations

import json
import re
from typing import Any

from core.state import Material, TopicProposal
from tools.debug_log import debug_log


def llm_infer_account_topics_and_generate_proposals(
    *,
    hot_titles: list[str],
    article_analysis: str,
    video_analysis: str,
    materials: list[Material],
    cfg: dict[str, Any],
    llm_chat: Any,
    extract_json_object: Any,
) -> tuple[list[TopicProposal], list[TopicProposal]]:
    """
    单次 LLM 调用：直接产出 Top5 proposals（含 rank），不再先生成 10 条候选再二次排序。

    约束：
    - 必须结构化 JSON 输出；不允许静默兜底。
    - 允许“格式修复型重试”（要求模型把原始输出整理成严格 JSON）。
    """

    base_content = (
        "你是一个法律/法治方向的短视频选题策划。\n"
        "我们的账号主题：法律/法治（偏法规解读、案例分析、严谨、可验证）。\n"
        "你需要基于当前热点的详细事件总结来发散选题，不能脱离事件乱想。\n\n"
        "任务：请直接输出 5 个选题（Top5），并给出 1-5 的 rank（1 最好）。\n"
        "每条 proposal 必须包含：\n"
        "- proposal_id: \"A\"/\"B\"/\"C\"/\"D\"/\"E\"（表示 Top5 的排序名次，必须齐全且不重复）\n"
        "- rank: 1-5（必须齐全且不重复，且与 proposal_id 顺序一致：A=1,B=2,...,E=5）\n"
        "- title: 选题标题（尽量 18 个字以内）\n"
        "- thesis: 核心论点（1 句话，尽量 35 个字以内）\n"
        "- score: 0-100 综合分\n"
        "- selection_reason: 1-2 句话，解释为什么它能排在该位置\n\n"
        "排序优先级（候选排序与 Top5 均遵循）：\n"
        "1) 话题度和传播潜力\n"
        "2) 与账号定位的匹配度（法律/法治，偏法规解读、案例分析、严谨、可验证）\n"
        "3) 是否直接回应观众对当前热点最想知道的问题\n"
        "4) 是否容易继续搜集到可验证素材，适合后续脚本生产\n\n"
        "硬要求：\n"
        "1) 5 个选题必须明显不同，禁止同义改写凑数\n"
        "2) 全部中文、严谨、可验证、不八卦、不标题党，基调积极正面、服务国内观众\n"
        "3) 输出必须是 JSON 对象且只包含一个字段：proposals\n"
        "   - proposals 为长度=5 的数组\n"
        "不要解释、不要 Markdown、不要 <think>、不要思考过程。\n\n"
        f"热点标题：{json.dumps(hot_titles, ensure_ascii=False)}\n"
        f"热点详细事件：{(article_analysis or '')[:3200]}\n"
        f"视频素材提炼(截断)：{(video_analysis or '')[:2000]}\n"
        f"素材摘要(最多10条)：{json.dumps((materials or [])[:10], ensure_ascii=False)}\n"
    )

    system = {
        "role": "system",
        "content": (
            "你是一个只输出 JSON 的程序。严格遵守：\n"
            "1) 只输出 JSON，不要解释，不要 Markdown，不要 <think>。\n"
            "2) 输出必须是 JSON 对象，且只能包含 proposals 一个字段。\n"
        ),
    }

    def _validate(obj: dict[str, Any]) -> tuple[list[TopicProposal], list[TopicProposal]] | None:
        proposals_raw = obj.get("proposals")
        if not isinstance(proposals_raw, list) or len(proposals_raw) != 5:
            return None

        proposals: list[TopicProposal] = []
        seen_pid: set[str] = set()
        seen_rank: set[int] = set()
        valid_ids = ("A", "B", "C", "D", "E")
        expected_rank_by_pid = {"A": 1, "B": 2, "C": 3, "D": 4, "E": 5}
        for it in proposals_raw:
            if not isinstance(it, dict):
                return None
            pid = str(it.get("proposal_id") or "").strip().upper()
            title = str(it.get("title") or "").strip()
            thesis = str(it.get("thesis") or "").strip()
            reason = str(it.get("selection_reason") or "").strip()
            if pid not in valid_ids or pid in seen_pid:
                return None
            if not title or not thesis or not reason:
                return None
            if "..." in title or title in ("...", "…"):
                return None
            try:
                rank = int(str(it.get("rank") or "").strip())
                score = int(str(it.get("score") or "").strip())
            except Exception:
                return None
            if expected_rank_by_pid.get(pid) != rank:
                return None
            if rank < 1 or rank > 5 or score < 0 or score > 100:
                return None
            if rank in seen_rank:
                return None
            seen_pid.add(pid)
            seen_rank.add(rank)
            proposals.append(
                {
                    "proposal_id": pid,
                    "candidate_id": pid,
                    "rank": rank,
                    "score": score,
                    "title": title,
                    "thesis": thesis,
                    "outline": [],
                    "selection_reason": reason,
                }
            )

        if len(proposals) != 5:
            return None
        proposals_sorted = sorted(proposals, key=lambda x: int(x.get("rank") or 0))
        # 兼容返回：proposal_candidates 与 proposals 同源（均为 Top5）
        proposal_candidates = [
            {
                "proposal_id": str(p.get("proposal_id") or ""),
                "candidate_id": str(p.get("candidate_id") or ""),
                "title": str(p.get("title") or ""),
                "thesis": str(p.get("thesis") or ""),
                "rank": int(p.get("rank") or 0),
                "score": int(p.get("score") or 0),
                "selection_reason": str(p.get("selection_reason") or ""),
            }
            for p in proposals_sorted
        ]
        return (proposal_candidates, proposals_sorted)

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
        debug_log(f"op=infer_topics_and_generate_proposals attempt={attempt}", cfg=cfg)
        text = llm_chat([system, prompt], cfg=local_cfg) or ""
        last_text = text
        obj = extract_json_object(last_text) or {}
        if isinstance(obj, dict):
            ok = _validate(obj)
            if ok:
                return ok
        last_issue = "缺少 proposals 或长度不对，或字段校验失败"

        # 修复型重试：要求模型把上次原始输出整理成严格 JSON（仍是真实 LLM 调用）
        snippet = re.sub(r"\s+", " ", (last_text or "")).strip()[:1600]
        repair_prompt = {
            "role": "user",
            "content": (
                "把下面内容整理成严格 JSON 对象，只能包含 proposals 一个字段。\n"
                "proposals 必须 5 条且每条包含 proposal_id/rank/title/thesis/score/selection_reason。\n"
                "只输出 JSON，不要解释/Markdown/<think>。\n\n"
                + base_content
                + "\n上次原始输出："
                + snippet
            ),
        }
        repaired = llm_chat([system, repair_prompt], cfg={**local_cfg, "minimax_reasoning_split": "0"}) or ""
        last_text = repaired or last_text
        obj2 = extract_json_object(last_text) or {}
        if isinstance(obj2, dict):
            ok2 = _validate(obj2)
            if ok2:
                return ok2

        last_issue = "修复型重试仍未通过校验"

    snippet = re.sub(r"\s+", " ", (last_text or "")).strip()[:280]
    raise RuntimeError(f"LLM 融合选题输出不合规（期望 proposals JSON）：{snippet!r}")
