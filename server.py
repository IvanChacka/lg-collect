from __future__ import annotations

from datetime import date
from typing import Any

import asyncio
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, PlainTextResponse

from core.runtime import build_app, load_env, make_config
from core.state import initial_state
from core.viz import mermaid as graph_mermaid
from tools.utils import new_thread_id


app = FastAPI(title="hot-collect")
compiled = build_app(use_sqlite=True)


@app.post("/run/daily")
async def run_daily(payload: dict[str, Any] | None = None):
    # 允许在服务常驻期间修改 .env 立即生效（例如切换 HOT_SOURCE）
    load_env()
    payload = payload or {}
    run_date = payload.get("run_date") or date.today().isoformat()
    thread_id = payload.get("thread_id") or new_thread_id(run_date=run_date)

    state = initial_state(thread_id=thread_id, run_date=run_date)
    hotspot = str(payload.get("hotspot") or "").strip()
    if hotspot:
        state["direct_hotspot_keyword"] = hotspot
    cfg = make_config(thread_id=thread_id, run_date=run_date)

    # 避免在 ASGI 事件循环内做阻塞调用（包括飞书 WS connect / LLM / 爬虫等）
    result = await asyncio.to_thread(compiled.invoke, state, config=cfg)
    return {"thread_id": thread_id, "status": result.get("status"), "result": result}


@app.get("/graph/mermaid")
async def graph_mermaid_text():
    return PlainTextResponse(
        graph_mermaid(lang="zh", direction="TB", curve="stepAfter", node_spacing=70, rank_spacing=90)
    )


@app.get("/graph", response_class=HTMLResponse)
async def graph_page():
    """
    简单的开发期可视化页面：用 Mermaid 渲染当前工作流。
    """

    html = """<!doctype html>
<html>
  <head>
    <meta charset="utf-8" />
    <title>hot-collect 工作流</title>
    <style>
      body { font-family: ui-sans-serif, system-ui, -apple-system; margin: 0; padding: 16px; }
      .box { border: 1px solid #eee; border-radius: 12px; padding: 12px; }
      pre { white-space: pre-wrap; word-break: break-word; }
    </style>
  </head>
  <body>
    <h2>hot-collect 工作流（Mermaid）</h2>
    <div class="box">
      <div class="mermaid" id="m"></div>
      <details style="margin-top: 12px;">
        <summary>查看 Mermaid 源码</summary>
        <pre id="src"></pre>
      </details>
    </div>
    <script type="module">
      import mermaid from "https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.esm.min.mjs";
      mermaid.initialize({ startOnLoad: false });
      const resp = await fetch("/graph/mermaid");
      const text = await resp.text();
      document.getElementById("m").textContent = text;
      document.getElementById("src").textContent = text;
      await mermaid.run({ querySelector: ".mermaid" });
    </script>
  </body>
</html>
"""
    return HTMLResponse(html)
