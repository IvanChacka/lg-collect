from __future__ import annotations

"""
Standalone runner (human-in-the-loop via Feishu WS).

This script runs the whole workflow in a plain Python process.
It will block at the "send_feishu_card_and_wait_selection" node until the user replies
in Feishu with "@机器人 + 序号".

Usage:
  python scripts/feishu_listen_and_resume.py
"""

import os
import sys
from datetime import date

# Ensure repo root is on sys.path when executed as `python scripts/...`.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from core.runtime import build_app, load_env, make_config
from core.state import initial_state
from tools.utils import get_config, new_thread_id


def main() -> None:
    # Always reload .env before starting
    load_env()
    cfg = get_config({})

    # Hard fail early if Feishu config is missing
    app_id = str(cfg.get("feishu_app_id") or "").strip()
    app_secret = str(cfg.get("feishu_app_secret") or "").strip()
    chat_id = str(cfg.get("feishu_chat_id") or "").strip()
    ws_enable = str(cfg.get("feishu_ws_enable") or "0").strip().lower() in ("1", "true", "yes", "on")
    if not app_id or not app_secret or not chat_id:
        raise SystemExit("Feishu 配置缺失：请在 .env 中配置 FEISHU_APP_ID / FEISHU_APP_SECRET / FEISHU_CHAT_ID")
    if not ws_enable:
        raise SystemExit("FEISHU_WS_ENABLE=0，无法在 human_mode 下等待飞书选择")

    compiled = build_app(use_sqlite=True)
    run_date = date.today().isoformat()
    thread_id = new_thread_id(run_date=run_date)

    state = initial_state(thread_id=thread_id, run_date=run_date)
    invoke_cfg = make_config(thread_id=thread_id, run_date=run_date, human_mode=True)

    print(f"thread_id={thread_id} start")
    out = compiled.invoke(state, config=invoke_cfg)
    print(f"thread_id={thread_id} status={out.get('status')}")


if __name__ == "__main__":
    main()
