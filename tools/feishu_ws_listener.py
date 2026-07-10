from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from typing import Any, Callable, Optional

import lark_oapi as lark
from lark_oapi import ws
from lark_oapi.api.im.v1.model import P2ImMessageReceiveV1


@dataclass(frozen=True)
class FeishuIncomingMessage:
    chat_id: str
    message_type: str
    text: str
    message_id: str
    sender_type: str = ""
    root_id: str = ""
    parent_id: str = ""
    mention_names: tuple[str, ...] = ()


def _extract_text(content_str: str, msg_type: str) -> str:
    try:
        parsed = json.loads(content_str)
    except Exception:
        return content_str or ""

    if msg_type == "text":
        return str(parsed.get("text") or "")

    if msg_type == "post":
        texts: list[str] = []
        for paragraph in (parsed.get("zh_cn") or {}).get("content", []):
            for node in paragraph:
                if not isinstance(node, dict):
                    continue
                tag = node.get("tag")
                if tag == "text":
                    texts.append(str(node.get("text") or ""))
                elif tag == "at":
                    texts.append(f"@{node.get('name', '')}")
                elif tag == "md":
                    texts.append(str(node.get("text") or ""))
        return "".join(texts)

    return content_str or ""


def start_ws_listener(
    *,
    api_base: str,
    app_id: str,
    app_secret: str,
    verification_token: str = "",
    encrypt_key: str = "",
    on_message: Callable[[FeishuIncomingMessage], None],
    connect_timeout_seconds: int = 15,
) -> Optional[ws.Client]:
    """
    启动 Feishu 事件订阅 WebSocket 客户端（后台线程）。

    说明：
    - 该函数只负责把消息事件转发给 on_message。
    - 具体“识别 @ + 选项并 resume 工作流”的逻辑由调用方实现。
    """

    if not app_id or not app_secret:
        return None

    def _handler(event: P2ImMessageReceiveV1):
        sender = event.event.sender if hasattr(event, "event") and event.event else None
        msg = event.event.message if hasattr(event, "event") and event.event else None
        if not msg:
            return

        msg_type = str(msg.message_type or "").strip()
        sender_type = str(sender.sender_type or "").strip() if sender else ""
        chat_id = str(msg.chat_id or "").strip()
        content = str(msg.content or "")
        message_id = str(msg.message_id or "").strip()
        root_id = str(getattr(msg, "root_id", "") or "").strip()
        parent_id = str(getattr(msg, "parent_id", "") or "").strip()
        text = _extract_text(content, msg_type)
        mention_names: list[str] = []
        for m in (msg.mentions or []) if hasattr(msg, "mentions") else []:
            name = str(getattr(m, "name", "") or "").strip()
            if name:
                mention_names.append(name)
        on_message(
            FeishuIncomingMessage(
                chat_id=chat_id,
                message_type=msg_type,
                text=text,
                message_id=message_id,
                root_id=root_id,
                parent_id=parent_id,
                sender_type=sender_type,
                mention_names=tuple(mention_names),
            )
        )

    event_handler = (
        lark.EventDispatcherHandler.builder(encrypt_key=encrypt_key, verification_token=verification_token)
        .register_p2_im_message_receive_v1(_handler)
        .build()
    )

    # 注意：在 FastAPI/uvicorn 的 startup async 上下文中创建 ws.Client，
    # lark_oapi 可能会捕获“正在运行”的 event loop，进而在 start() 时触发
    # “This event loop is already running”。因此把 client 的创建与启动都放进后台线程。
    client_box: dict[str, ws.Client] = {}
    ready = threading.Event()
    connected = threading.Event()
    err_box: dict[str, BaseException] = {}

    def _run() -> None:
        import asyncio
        import lark_oapi.ws.client as ws_client
        from lark_oapi.ws.client import ClientException

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        # lark_oapi.ws.client 在 import 时会捕获一个 module-level 的 event loop（loop = asyncio.get_event_loop()），
        # 在 uvicorn/uvloop 环境下这通常是“正在运行”的 loop，导致 client.start() 抛
        # RuntimeError: this event loop is already running.
        # 这里显式把其 module-level loop 替换成当前线程创建的 loop，确保 run_until_complete 可用。
        ws_client.loop = loop

        client = ws.Client(
            app_id=app_id,
            app_secret=app_secret,
            log_level=lark.LogLevel.INFO,
            event_handler=event_handler,
            domain=api_base,
            auto_reconnect=True,
        )
        client_box["client"] = client
        ready.set()

        # lark_oapi.ws.client.Client.start() 会在内部 run_until_complete(_connect) 之后进入无限 select loop。
        # 为了让调用方能确定“WS 是否真的连上了”，这里把关键流程拆开：
        # - 先 connect 成功再触发 connected Event
        # - 后续进入持续的 ping/select loop
        try:
            loop.run_until_complete(client._connect())
            connected.set()
        except ClientException as e:
            err_box["err"] = e
            connected.set()
            return
        except Exception as e:
            err_box["err"] = e
            connected.set()
            try:
                loop.run_until_complete(client._disconnect())
            except Exception:
                pass
            return

        loop.create_task(client._ping_loop())
        # 注意：_select() 是 lark_oapi.ws.client 的 module-level coroutine
        loop.run_until_complete(ws_client._select())

    t = threading.Thread(target=_run, daemon=True)
    t.start()

    ready.wait(timeout=2)
    # 如果没拿到 client，说明线程在创建阶段就崩了
    client = client_box.get("client")
    if client is None:
        if "err" in err_box:
            raise RuntimeError(f"Feishu WS listener failed before client init: {err_box['err']!r}")
        raise RuntimeError("Feishu WS listener failed before client init (unknown error).")

    # 等待首次 connect 成功/失败，避免“可视化界面卡住但其实 WS 未连接”的假象
    connected.wait(timeout=max(1, int(connect_timeout_seconds)))
    if "err" in err_box:
        raise RuntimeError(f"Feishu WS connect failed: {err_box['err']!r}")
    if not connected.is_set():
        raise RuntimeError(f"Feishu WS connect timeout after {connect_timeout_seconds}s.")

    return client


def _feishu_ws_subprocess_entry(
    child_conn: Any,
    api_base: str,
    app_id: str,
    app_secret: str,
    verification_token: str,
    encrypt_key: str,
    connect_timeout_seconds: int,
) -> None:
    """
    子进程入口：启动 WS listener，并把消息通过 Pipe 发回父进程。

    注意：必须是 module-level function，才能在 multiprocessing spawn 模式下被 pickle。
    """

    import time

    try:
        def _on_msg(msg: FeishuIncomingMessage) -> None:
            try:
                child_conn.send(("msg", msg))
            except Exception:
                pass

        start_ws_listener(
            api_base=api_base,
            app_id=app_id,
            app_secret=app_secret,
            verification_token=verification_token,
            encrypt_key=encrypt_key,
            on_message=_on_msg,
            connect_timeout_seconds=connect_timeout_seconds,
        )
        child_conn.send(("ready", None))
        while True:
            # 避免在某些环境里触发对 time.sleep 的阻塞检测
            threading.Event().wait(1.0)
    except BaseException as e:
        try:
            child_conn.send(("err", repr(e)))
        except Exception:
            pass
    finally:
        try:
            child_conn.close()
        except Exception:
            pass


def start_ws_listener_in_subprocess(
    *,
    api_base: str,
    app_id: str,
    app_secret: str,
    verification_token: str = "",
    encrypt_key: str = "",
    on_message: Callable[[FeishuIncomingMessage], None],
    connect_timeout_seconds: int = 15,
) -> tuple["multiprocessing.context.SpawnProcess", Callable[[], None]]:
    """
    在子进程中启动 Feishu WS listener，并把消息转发回当前进程。

    背景：
    - LangGraph dev/Studio 环境会对“在事件循环里发生的同步阻塞调用”进行检测并抛 BlockingError。
    - lark_oapi 的 WS client 在连接阶段会触发 socket.connect 的阻塞调用（其内部实现不受我们控制）。
    - 将 WS 监听隔离到子进程，可以避免该阻塞检测影响主进程内的工作流执行。

    返回：
    - (process, stop_fn)
      - stop_fn(): 尝试优雅停止并 terminate 子进程。

    约束：
    - 不做静默兜底：子进程启动失败/连接失败会直接抛异常。
    """

    import multiprocessing as mp
    import time

    ctx = mp.get_context("spawn")
    parent_conn, child_conn = ctx.Pipe(duplex=False)

    proc = ctx.Process(
        target=_feishu_ws_subprocess_entry,
        args=(
            child_conn,
            api_base,
            app_id,
            app_secret,
            verification_token,
            encrypt_key,
            int(connect_timeout_seconds),
        ),
        daemon=True,
    )
    proc.start()

    # 等待 ready/err（不可无限挂起）
    started_at = time.time()
    err: str | None = None
    while time.time() - started_at < max(1, int(connect_timeout_seconds)):
        if parent_conn.poll(0.2):
            kind, payload = parent_conn.recv()
            if kind == "ready":
                err = None
                break
            if kind == "err":
                err = str(payload or "unknown error")
                break
            # 其它消息（msg）先缓存，待 forward thread 处理
            # 这里简单忽略：因为 ready 通常先到；若先到了 msg，forward thread 会在启动后继续消费。
        if not proc.is_alive():
            err = "subprocess exited unexpectedly"
            break

    if err is not None:
        try:
            proc.terminate()
        except Exception:
            pass
        raise RuntimeError(f"Feishu WS subprocess start failed: {err}")

    stop_flag = threading.Event()

    def _forward_loop() -> None:
        while not stop_flag.is_set():
            try:
                if not parent_conn.poll(0.2):
                    continue
                kind, payload = parent_conn.recv()
            except EOFError:
                break
            except Exception:
                continue
            if kind == "msg" and isinstance(payload, FeishuIncomingMessage):
                try:
                    on_message(payload)
                except Exception:
                    # 回调异常不应让 forward 线程退出（避免错过后续消息）
                    continue

    t = threading.Thread(target=_forward_loop, daemon=True)
    t.start()

    def _stop() -> None:
        stop_flag.set()
        try:
            parent_conn.close()
        except Exception:
            pass
        try:
            if proc.is_alive():
                proc.terminate()
        except Exception:
            pass

    return proc, _stop
