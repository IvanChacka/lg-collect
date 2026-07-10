from typing import Any

from langchain_core.runnables import RunnableConfig

from core.state import HotCollectState
from tools.feishu_api import send_text_message
from tools.feishu_pending import get_chat_id_by_thread_id, is_ack_sent, mark_ack_sent
from tools.llm_client import llm_build_material_queries
from tools.utils import get_config


def prepare_selected_topic_materials_node(
    state: HotCollectState, config: RunnableConfig | None = None
) -> dict[str, Any]:
    """
    节点：按“已选题目”重建素材收集任务的起点状态。

    为什么需要它：
    - AI 生成的选题标题往往更“吸引人/抽象/模糊”，直接丢给搜索（浏览器 / B 站）可能命中率低；
    - 因此这里会调用 LLM 基于 title/thesis/outline（以及可选的 hot_keyword、run_date）
      生成 3 个更可检索的 query（事件/机制/影响三种侧重点），写入 state.filtered_keywords；
    - 下游 `search_articles_selected` 与 `search_videos_selected` 两条支路会分别使用这 3 个 query
      去搜索文章与视频素材，提升“能搜到真实资料”的概率与覆盖面。

    注意：
    - 本节点只负责产出“已选题目阶段”的检索 query 列表（写入 filtered_keywords）。
    - 不在这里“批量清空”其他字段：清空行为应由需要它的节点显式完成，
      避免出现大量无意义的空字段 patch，且避免误删下游仍需使用的状态。
    """
    cfg = get_config(config)
    proposal_id = (state.get("selected_proposal_id") or "").strip()
    proposals = {str(p.get("proposal_id") or "").strip(): p for p in (state.get("proposals") or [])}
    proposal = proposals.get(proposal_id)
    if not proposal_id or proposal is None:
        raise RuntimeError(f"prepare_selected_topic_materials 找不到选中的 proposal: {proposal_id!r}")

    selected_title = str(proposal.get("title") or "").strip()
    if not selected_title:
        raise RuntimeError(f"prepare_selected_topic_materials 选中的 proposal 缺少 title: {proposal_id!r}")

    run_date = str(state.get("run_date") or cfg.get("run_date") or "").strip()
    hot_keyword = str(state.get("selected_hot_keyword") or "").strip()
    thread_id = str(state.get("thread_id") or cfg.get("thread_id") or "").strip()

    # 飞书回执：放在 prepare_selected_topic_materials（即真正开始执行“选中后”链路时）
    # 不放在 send_feishu_card_and_wait_selection，避免“看似接收但实际未续跑”的错觉。
    if thread_id and not is_ack_sent(thread_id=thread_id):
        chat_id = get_chat_id_by_thread_id(thread_id=thread_id)
        if chat_id:
            try:
                # 尽可能简洁：携带选中标题，并明确后续工作流开始运行
                send_text_message(cfg=cfg, text=f"已选：《{selected_title}》。后续流程已开始。", chat_id=chat_id)
                mark_ack_sent(thread_id=thread_id)
            except Exception:
                # 回执失败不应中断素材准备；但也不做静默“成功”标记
                pass

    # 约束：生成的 query 必须是“对热点的增量补充信息”，并且不得与热榜词条重复。
    # 为了让 LLM 能显式避开热榜列表，这里把 hot_titles 注入到 proposal 里作为上下文输入；
    # 不改变 state.proposals 的原始内容。
    hot_titles = state.get("hot_titles") or []
    proposal_ctx = {**proposal, "hot_titles": hot_titles}
    queries = llm_build_material_queries(
        proposal=proposal_ctx, hot_keyword=hot_keyword, run_date=run_date, cfg=cfg
    )

    # 选题标题可能偏“标题党/抽象”，这里用 LLM 把它转成 3 个更可检索的 query，
    # 下游文章/视频两条支路会分别使用这 3 个 query 做素材搜索。
    patch: dict[str, Any] = {"filtered_keywords": queries}
    # 对外可观察的阶段标记：选题已确定且后续素材检索即将开始。
    if state.get("status") != "selected":
        patch["status"] = "selected"
    return patch
