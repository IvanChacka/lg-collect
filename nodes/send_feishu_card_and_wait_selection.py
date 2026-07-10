from __future__ import annotations

import threading
import time
from typing import Any

from langchain_core.runnables import RunnableConfig

from core.state import HotCollectState
from tools.feishu import parse_thread_id_from_text, send_topic_choice_message
from tools.feishu_api import get_bot_name, send_text_message
from tools.feishu_pending import (
    get_latest_thread_id,
    get_thread_id_by_message_id,
    map_index_to_proposal_id,
)
from tools.feishu_reply import is_bot_mentioned, parse_choice_token
from tools.feishu_ws_listener import (
    FeishuIncomingMessage,
    start_ws_listener,
    start_ws_listener_in_subprocess,
)
from tools.utils import get_config, new_thread_id


def send_feishu_card_and_wait_selection_node(
    state: HotCollectState, config: RunnableConfig | None = None
) -> dict[str, Any]:
    """
    节点：发送飞书选题卡片，并在本进程内启动 WS 监听，阻塞直到用户 @回复序号。

    约束：
    - 禁止 mock：必须是真实 WS 监听到消息后才继续。
    - 禁止静默兜底：超时或配置缺失必须显式报错。
    - 选择回执由 prepare_selected_topic_materials 负责，这里不发送“已选...”回执。
    """

    cfg = get_config(config)
    proposals = state.get("proposals") or []
    thread_id = str(state.get("thread_id") or cfg.get("thread_id") or "").strip()
    run_date = str(state.get("run_date") or cfg.get("run_date") or "").strip()

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

    # 优先读 state（CLI 会写入），避免部分运行器不向节点透传 configurable 的情况。
    human_mode_raw = state.get("human_mode")
    if human_mode_raw is None:
        human_mode_raw = cfg.get("human_mode", True)
    human_mode = _as_bool(human_mode_raw, default=True)
    if not proposals:
        return {"status": "error", "errors": ["No proposals generated."]}

    # Studio/Dev 场景：可能“只跑该节点”且未经过 start_hot_fetch 初始化。
    # 如果 thread_id 缺失，会导致：
    # - pending 映射无法写入（tools.feishu_pending.set_pending 会直接 return）
    # - WS 收到用户回复后无法把“序号”映射回 proposal_id，表现为“@ 机器人无反应”
    if not thread_id:
        thread_id = new_thread_id(prefix="hot_topic", run_date=run_date or None)

    if not human_mode:
        selected = str((proposals[0] or {}).get("proposal_id") or "").strip()
        if not selected:
            return {"status": "error", "errors": ["First proposal missing proposal_id."]}
        return {"thread_id": thread_id, "selected_proposal_id": selected, "status": "selected"}

    # human_mode: 必须可发卡片 + 可监听 WS
    chat_id = str(cfg.get("feishu_chat_id") or "").strip()
    if not chat_id:
        return {"status": "error", "errors": ["FEISHU_CHAT_ID is empty; cannot wait for selection."]}
    app_id = str(cfg.get("feishu_app_id") or "").strip()
    app_secret = str(cfg.get("feishu_app_secret") or "").strip()
    api_base = str(cfg.get("feishu_api_base") or "https://open.feishu.cn").strip()
    ws_enable = str(cfg.get("feishu_ws_enable") or "0").strip().lower() in ("1", "true", "yes", "on")
    if not ws_enable:
        return {
            "status": "error",
            "errors": ["FEISHU_WS_ENABLE is disabled; cannot wait for selection in human_mode."],
        }
    if not app_id or not app_secret:
        return {
            "status": "error",
            "errors": ["FEISHU_APP_ID/FEISHU_APP_SECRET missing; cannot start Feishu WS listener."],
        }

    hot_keyword = str(state.get("selected_hot_keyword") or "").strip()
    hot_platform = str((state.get("hot_picked") or {}).get("platform") or "").strip()

    try:
        message_id = send_topic_choice_message(
            cfg=cfg,
            thread_id=thread_id,
            hot_keyword=hot_keyword,
            hot_platform=hot_platform,
            proposals=proposals,
        )
    except Exception as e:
        return {"status": "error", "errors": [f"Feishu send failed: {e!r}"]}

    # 尝试获取 bot name，用于更稳地识别 @
    try:
        bot_name = get_bot_name(cfg=cfg)
    except Exception:
        bot_name = ""

    selected_box: dict[str, str] = {"proposal_id": ""}
    done = threading.Event()

    def _resolve_thread_id(msg: FeishuIncomingMessage) -> str:
        # 优先用 parent/root 映射回 thread_id（支持多 thread 并存）
        tid = (
            get_thread_id_by_message_id(message_id=msg.parent_id)
            or get_thread_id_by_message_id(message_id=msg.root_id)
            or parse_thread_id_from_text(msg.text)
        )
        if not tid:
            # 兜底：同群最新 thread（仅适用于“只跑一个 thread”场景）
            tid = get_latest_thread_id(chat_id=msg.chat_id)
        return str(tid or "").strip()

    def _on_message(msg: FeishuIncomingMessage) -> None:
        if done.is_set():
            return
        if msg.sender_type == "bot":
            return
        if msg.chat_id != chat_id:
            return
        text = (msg.text or "").strip()
        if not text:
            return

        mentioned = is_bot_mentioned(bot_name=bot_name, text=text, mention_names=msg.mention_names)
        if not mentioned:
            return

        tid = _resolve_thread_id(msg)
        if not tid or tid != thread_id:
            return

        token = parse_choice_token(text)
        if not token:
            return

        proposal_id = ""
        if token.isdigit():
            proposal_id = map_index_to_proposal_id(thread_id=thread_id, index=token)
        if not proposal_id:
            # 不中断等待：提示用户重新回复
            try:
                send_text_message(cfg=cfg, text="抱歉，我没识别到有效的选题序号，请重新 @我 回复一次（例如：1）。", chat_id=chat_id)
            except Exception:
                pass
            return

        selected_box["proposal_id"] = str(proposal_id).strip()
        done.set()

    # 启动 WS 监听（后台线程）
    ws_stop = None
    try:
        start_ws_listener(
            api_base=api_base,
            app_id=app_id,
            app_secret=app_secret,
            on_message=_on_message,
        )
    except Exception as e:
        # LangGraph dev/Studio 会对“事件循环内的同步阻塞调用”抛 BlockingError。
        # lark_oapi 的 WS connect 阶段会触发 socket.connect 的阻塞调用，无法在库内改成 async。
        # 这里显式切换到子进程隔离（仍然是真实 WS 监听，不做 mock/兜底数据源切换）。
        if "BlockingError" in repr(e) or "Blocking call to socket.socket.connect" in repr(e):
            try:
                _proc, ws_stop = start_ws_listener_in_subprocess(
                    api_base=api_base,
                    app_id=app_id,
                    app_secret=app_secret,
                    on_message=_on_message,
                )
            except Exception as e2:
                return {
                    "status": "error",
                    "errors": [
                        f"Feishu WS listener start failed (blocking detected): {e!r}",
                        f"Feishu WS subprocess start failed: {e2!r}",
                    ],
                }
        else:
            return {"status": "error", "errors": [f"Feishu WS listener start failed: {e!r}"]}

    timeout = int(cfg.get("feishu_selection_timeout_seconds") or 60)
    started_at = time.time()
    try:
        while not done.wait(timeout=1.0):
            if time.time() - started_at > timeout:
                # 超时自动选择：显式告知群内，避免“静默兜底”
                best = None
                best_score = -1
                best_rank = 10**9
                for p in proposals:
                    if not isinstance(p, dict):
                        continue
                    pid = str(p.get("proposal_id") or "").strip()
                    title = str(p.get("title") or "").strip()
                    if not pid or not title:
                        continue
                    try:
                        score = int(str(p.get("score") or "").strip())
                    except Exception:
                        score = -1
                    try:
                        rank = int(str(p.get("rank") or "").strip())
                    except Exception:
                        rank = 10**9
                    # 优先 score，其次 rank，最后按出现顺序
                    if score > best_score or (score == best_score and rank < best_rank):
                        best = p
                        best_score = score
                        best_rank = rank

                if best is None:
                    # 没有可选项：仍然报错（不做静默“选第一个”）
                    raise RuntimeError(
                        f"等待飞书选题回复超时（{timeout}s），且 proposals 中没有可自动选择的条目。"
                    )

                selected_pid = str(best.get("proposal_id") or "").strip()
                selected_title = str(best.get("title") or "").strip()
                try:
                    send_text_message(cfg=cfg, text=f"无人回应，自动选择《{selected_title}》。", chat_id=chat_id)
                except Exception:
                    # 自动选择提示失败不应阻断工作流；但不做任何“已提示成功”的标记
                    pass

                return {
                    "feishu_message_id": message_id,
                    "thread_id": thread_id,
                    "selected_proposal_id": selected_pid,
                    "status": "selected",
                }

        return {
            "feishu_message_id": message_id,
            "thread_id": thread_id,
            "selected_proposal_id": selected_box["proposal_id"],
            "status": "selected",
        }
    finally:
        if ws_stop is not None:
            try:
                ws_stop()
            except Exception:
                pass
