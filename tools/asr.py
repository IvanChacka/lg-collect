from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable

import httpx

from tools.debug_log import debug_log


SAMPLE_RATE = 16000


def srt_to_text(srt: str) -> str:
    """
    非严格解析：去掉序号与时间戳，保留字幕文本（供“优先字幕”策略使用）。
    """
    import re

    lines: list[str] = []
    for raw in (srt or "").splitlines():
        ln = raw.strip()
        if not ln:
            continue
        if ln.isdigit():
            continue
        if re.match(r"^\\d{2}:\\d{2}:\\d{2}[,.]\\d{3}\\s+-->\\s+\\d{2}:\\d{2}:\\d{2}", ln):
            continue
        lines.append(ln)
    return "\n".join(lines).strip()


def _http_date() -> str:
    # RFC 1123 date, e.g. "Wed, 29 Apr 2026 03:00:00 GMT"
    return datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S GMT")


def _sha256_digest(data: bytes) -> str:
    return "SHA-256=" + base64.b64encode(hashlib.sha256(data).digest()).decode("utf-8")


def _hmac_sha256_base64(secret: str, message: str) -> str:
    sig = hmac.new(secret.encode("utf-8"), message.encode("utf-8"), digestmod=hashlib.sha256).digest()
    return base64.b64encode(sig).decode("utf-8")


def _make_auth_headers(
    *,
    api_key: str,
    api_secret: str,
    host: str,
    method: str,
    uri: str,
    body_bytes: bytes,
    content_type: str,
) -> dict[str, str]:
    """
    讯飞极速语音转写（OST）HTTP鉴权：HMAC-SHA256 + Digest。
    """
    date = _http_date()
    digest = _sha256_digest(body_bytes)
    signature_origin = f"host: {host}\ndate: {date}\n{method} {uri} HTTP/1.1\ndigest: {digest}"
    signature = _hmac_sha256_base64(api_secret, signature_origin)
    authorization = (
        f'api_key="{api_key}", algorithm="hmac-sha256", '
        f'headers="host date request-line digest", signature="{signature}"'
    )
    return {
        "Host": host,
        "Date": date,
        "Digest": digest,
        "Authorization": authorization,
        "Content-Type": content_type,
        "Accept": "application/json",
    }


def _encode_multipart(
    *,
    fields: dict[str, str],
    files: Iterable[tuple[str, str, str, bytes]],
) -> tuple[bytes, str]:
    boundary = "----hotcollect-" + uuid.uuid4().hex
    parts: list[bytes] = []

    def _add(s: str) -> None:
        parts.append(s.encode("utf-8"))

    for name, filename, content_type, content in files:
        _add(f"--{boundary}\r\n")
        _add(
            f'Content-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'
        )
        _add(f"Content-Type: {content_type}\r\n\r\n")
        parts.append(content)
        _add("\r\n")

    # 文档示例中 file 在前，字段在后；这里按该顺序编码，避免部分服务端严格解析失败。
    for k, v in fields.items():
        _add(f"--{boundary}\r\n")
        _add(f'Content-Disposition: form-data; name="{k}"\r\n\r\n')
        _add(str(v))
        _add("\r\n")

    _add(f"--{boundary}--\r\n")
    body = b"".join(parts)
    return body, f"multipart/form-data; boundary={boundary}"


def _convert_audio_to_mp3_16k(audio_path: str, *, out_path: str) -> None:
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    cmd = [
        "ffmpeg",
        "-v",
        "error",
        "-y",
        "-i",
        audio_path,
        "-ac",
        "1",
        "-ar",
        str(SAMPLE_RATE),
        "-codec:a",
        "libmp3lame",
        "-b:a",
        "32k",
        out_path,
    ]
    subprocess.run(cmd, check=True, capture_output=True, text=True)
    if not os.path.exists(out_path) or os.path.getsize(out_path) <= 0:
        raise RuntimeError("ffmpeg 转码失败：未产出 mp3 文件")


@dataclass(frozen=True)
class _SpeedEndpoints:
    upload_base: str = "https://upload-ost-api.xfyun.cn"
    ost_base: str = "https://ost-api.xfyun.cn"
    upload_uri: str = "/file/upload"
    pro_create_uri: str = "/v2/ost/pro_create"
    query_uri: str = "/v2/ost/query"


def _parse_speed_text(obj: dict[str, Any]) -> str:
    """
    解析 query 返回的 lattice/json_1best 为纯文本。
    """
    data = obj.get("data") or {}
    result = data.get("result") or {}
    lattice = result.get("lattice") or []
    parts: list[str] = []
    for item in lattice:
        if not isinstance(item, dict):
            continue
        j = item.get("json_1best")
        if not j:
            continue
        # json_1best 可能是 dict（新文档示例），也可能是 str（历史返回）
        j_obj: dict[str, Any] | None = None
        if isinstance(j, dict):
            j_obj = j
        elif isinstance(j, str):
            try:
                j_obj = json.loads(j)
            except Exception:
                j_obj = None
        if not j_obj:
            continue

        st = (((j_obj.get("st") or {}).get("rt")) or [])
        for rt_item in st:
            for ws_item in (rt_item.get("ws") or []) if isinstance(rt_item, dict) else []:
                for cw in (ws_item.get("cw") or []) if isinstance(ws_item, dict) else []:
                    w = str((cw or {}).get("w") or "")
                    if w:
                        parts.append(w)
    return "".join(parts).strip()


def _post_bytes(
    *,
    url: str,
    host: str,
    uri: str,
    method: str,
    body_bytes: bytes,
    content_type: str,
    api_key: str,
    api_secret: str,
    timeout_seconds: float,
    cfg: dict[str, Any] | None = None,
) -> dict[str, Any]:
    headers = _make_auth_headers(
        api_key=api_key,
        api_secret=api_secret,
        host=host,
        method=method,
        uri=uri,
        body_bytes=body_bytes,
        content_type=content_type,
    )
    with httpx.Client(timeout=timeout_seconds) as client:
        r = client.request(method, url, content=body_bytes, headers=headers)
        # OST 接口在部分“业务错误”（例如任务失败/无效 task_id）时会返回 HTTP 4xx，
        # 但 body 仍是标准 JSON（含 code/data/message）。这里优先尝试解析 JSON，
        # 交由上层按 code/task_status 做严格处理；非 JSON 再抛出 HTTP 错误。
        text = (r.text or "").strip()
        try:
            obj = r.json()
        except Exception:
            obj = None

        if r.status_code >= 400:
            # 绝不输出任何 API Key；这里只输出服务端回包与 HTTP 信息，便于定位 400/鉴权/参数问题。
            if isinstance(obj, dict):
                debug_log(
                    f"iflytek_ost http_error status={r.status_code} url={url!r} resp={json.dumps(obj, ensure_ascii=False)[:1200]}",
                    cfg=cfg,
                    prefix="asr",
                )
            else:
                debug_log(
                    f"iflytek_ost http_error status={r.status_code} url={url!r} resp_text={text[:1200]!r}",
                    cfg=cfg,
                    prefix="asr",
                )

        if r.status_code >= 400 and not isinstance(obj, dict):
            raise RuntimeError(f"极速语音转写 HTTP {r.status_code} for {url}: {text[:1200]}")

        if isinstance(obj, dict):
            # 透出 HTTP status，供上层轮询判断“持续 4xx/5xx”并快速失败，避免节点长时间卡住。
            obj.setdefault("_http_status", int(r.status_code))
            obj.setdefault("_url", url)
            return obj
        raise RuntimeError(f"极速语音转写返回非 JSON 响应 for {url}: {text[:1200]}")


def _upload_audio_speed(
    *,
    appid: str,
    api_key: str,
    api_secret: str,
    audio_path: str,
    cfg: dict[str, Any],
) -> str:
    """
    上传音频文件到 OST 存储，返回可用于 pro_create 的 audio_url。
    """
    eps = _SpeedEndpoints()
    request_id = uuid.uuid4().hex
    filename = os.path.basename(audio_path) or f"{request_id}.mp3"
    raw = open(audio_path, "rb").read()
    body, content_type = _encode_multipart(
        fields={"app_id": appid, "request_id": request_id},
        # 文档约定：上传字段名为 data（不是 file）
        files=[("data", filename, "application/octet-stream", raw)],
    )
    host = httpx.URL(eps.upload_base).host
    resp = _post_bytes(
        url=eps.upload_base + eps.upload_uri,
        host=host,
        uri=eps.upload_uri,
        method="POST",
        body_bytes=body,
        content_type=content_type,
        api_key=api_key,
        api_secret=api_secret,
        timeout_seconds=float(cfg.get("iflytek_speed_upload_timeout_seconds") or 120),
        cfg=cfg,
    )
    code = int(resp.get("code") or 0)
    if code != 0:
        raise RuntimeError(f"极速语音转写上传失败: {json.dumps(resp, ensure_ascii=False)[:1000]}")
    data = resp.get("data") or {}
    url = str(data.get("url") or "").strip()
    if not url:
        raise RuntimeError(f"极速语音转写上传未返回 url: {json.dumps(resp, ensure_ascii=False)[:1000]}")
    return url


def _create_task_speed(
    *,
    appid: str,
    api_key: str,
    api_secret: str,
    audio_url: str,
    cfg: dict[str, Any],
) -> str:
    eps = _SpeedEndpoints()
    host = httpx.URL(eps.ost_base).host

    # 关键参数：按文档约定传递 format/encoding，并附带 request_id。
    #
    # 注意：
    # - 本项目会把输入音频转码为 16k 单声道 mp3（见 _convert_audio_to_mp3_16k）
    # - 对应的 OST 参数应使用 `audio/mpeg` + `lame`（而不是 PCM 的 audio/L16;rate=16000 + raw）
    request_id = uuid.uuid4().hex
    req = {
        "common": {"app_id": appid},
        "business": {
            "request_id": request_id,
            "language": str(cfg.get("iflytek_speed_language") or "zh_cn").strip() or "zh_cn",
            "domain": str(cfg.get("iflytek_speed_domain") or "pro_ost_ed").strip() or "pro_ost_ed",
            "accent": str(cfg.get("iflytek_speed_accent") or "mandarin").strip() or "mandarin",
        },
        "data": {
            "audio_src": "http",
            "audio_url": audio_url,
            "encoding": "lame",
            "format": "audio/mpeg",
        },
    }
    debug_log(
        f"iflytek_ost pro_create request_id={request_id!r} language={req['business'].get('language')!r} domain={req['business'].get('domain')!r} format={req['data'].get('format')!r} encoding={req['data'].get('encoding')!r}",
        cfg=cfg,
        prefix="asr",
    )
    body = json.dumps(req, ensure_ascii=False).encode("utf-8")
    resp = _post_bytes(
        url=eps.ost_base + eps.pro_create_uri,
        host=host,
        uri=eps.pro_create_uri,
        method="POST",
        body_bytes=body,
        content_type="application/json; charset=utf-8",
        api_key=api_key,
        api_secret=api_secret,
        timeout_seconds=float(cfg.get("iflytek_speed_create_timeout_seconds") or 60),
        cfg=cfg,
    )
    code = int(resp.get("code") or 0)
    if code != 0:
        raise RuntimeError(f"极速语音转写建任务失败: {json.dumps(resp, ensure_ascii=False)[:1000]}")
    task_id = str((resp.get("data") or {}).get("task_id") or "").strip()
    if not task_id:
        raise RuntimeError(f"极速语音转写建任务未返回 task_id: {json.dumps(resp, ensure_ascii=False)[:1000]}")
    return task_id


def _query_task_speed(
    *,
    appid: str,
    api_key: str,
    api_secret: str,
    task_id: str,
    cfg: dict[str, Any],
) -> dict[str, Any]:
    eps = _SpeedEndpoints()
    host = httpx.URL(eps.ost_base).host
    # 注意：`/v2/ost/query` 不接受 business.force_refresh（会返回 code=10107 unknown field）。
    req = {"common": {"app_id": appid}, "business": {"task_id": task_id}}
    body = json.dumps(req, ensure_ascii=False).encode("utf-8")
    resp = _post_bytes(
        url=eps.ost_base + eps.query_uri,
        host=host,
        uri=eps.query_uri,
        method="POST",
        body_bytes=body,
        content_type="application/json; charset=utf-8",
        api_key=api_key,
        api_secret=api_secret,
        timeout_seconds=float(cfg.get("iflytek_speed_query_timeout_seconds") or 60),
        cfg=cfg,
    )
    return resp


def transcribe_audio(*, audio_path: str, cfg: dict[str, object]) -> str:
    """
    讯飞“极速语音转写”（HTTP 异步任务）：
    - 上传音频文件
    - 创建转写任务
    - 轮询查询结果并返回全文
    """
    appid = str(cfg.get("iflytek_appid") or "").strip()
    api_key = str(cfg.get("iflytek_api_key") or "").strip()
    api_secret = str(cfg.get("iflytek_api_secret") or "").strip()
    if not appid or not api_key or not api_secret:
        raise RuntimeError(
            "未设置讯飞 ASR 配置：请填写 IFLYTEK_APPID / IFLYTEK_API_KEY / IFLYTEK_API_SECRET"
        )

    tmp_dir = str(cfg.get("iflytek_speed_tmp_dir") or os.path.join(".data", "asr_speed_tmp"))
    os.makedirs(tmp_dir, exist_ok=True)
    tmp_mp3 = os.path.join(tmp_dir, hashlib.md5(audio_path.encode("utf-8")).hexdigest() + ".16k.mp3")
    if not os.path.exists(tmp_mp3) or os.path.getmtime(tmp_mp3) < os.path.getmtime(audio_path):
        _convert_audio_to_mp3_16k(audio_path, out_path=tmp_mp3)

    audio_url = _upload_audio_speed(
        appid=appid, api_key=api_key, api_secret=api_secret, audio_path=tmp_mp3, cfg=dict(cfg)
    )
    task_id = _create_task_speed(
        appid=appid, api_key=api_key, api_secret=api_secret, audio_url=audio_url, cfg=dict(cfg)
    )

    poll_interval = float(cfg.get("iflytek_speed_poll_interval_seconds") or 2.0)
    deadline_seconds = float(cfg.get("iflytek_speed_deadline_seconds") or 900.0)
    t0 = time.time()
    last_resp: dict[str, Any] | None = None
    consecutive_http_errors = 0
    last_status: int | None = None
    poll_n = 0

    while True:
        if time.time() - t0 > deadline_seconds:
            raise TimeoutError(f"极速语音转写超时（>{deadline_seconds}s） task_id={task_id}")
        resp = _query_task_speed(
            appid=appid,
            api_key=api_key,
            api_secret=api_secret,
            task_id=task_id,
            cfg=dict(cfg),
        )
        last_resp = resp
        poll_n += 1
        http_status = int(resp.get("_http_status") or 200)
        if http_status >= 400:
            consecutive_http_errors += 1
        else:
            consecutive_http_errors = 0

        # 连续 4xx/5xx 大概率是鉴权/参数错误或任务不可达；避免一直轮询导致节点“看起来卡死”。
        if consecutive_http_errors >= 3:
            raise RuntimeError(
                f"极速语音转写查询连续 HTTP 错误（{consecutive_http_errors} 次，last={http_status}） task_id={task_id}: "
                f"{json.dumps(resp, ensure_ascii=False)[:1000]}"
            )
        code = int(resp.get("code") or 0)
        data = resp.get("data") or {}
        raw_status = data.get("task_status", data.get("taskStatus", data.get("status", None)))
        try:
            status = int(raw_status or 0)
        except Exception:
            status = 0
        has_status = raw_status is not None

        if last_status is None or status != last_status or (poll_n % 10 == 0):
            debug_log(
                f"iflytek_ost poll n={poll_n} task_id={task_id} http={http_status} code={code} status={raw_status!r}",
                cfg=dict(cfg),
                prefix="asr",
            )
            last_status = status

        # 部分场景服务端会用非 0 code 表示“任务状态类错误”（并伴随 task_status），而不是网络/鉴权错误。
        # 这里不做静默兜底：仍严格以服务端返回为准，仅在可确定为“处理中”时继续轮询。
        if code != 0:
            if status == 4:
                raise RuntimeError(
                    f"极速语音转写任务失败（服务端 code={code} task_status=4） task_id={task_id}: "
                    f"{json.dumps(resp, ensure_ascii=False)[:1000]}"
                )
            if has_status and status in (0, 1, 2):
                # 仍在处理：继续等待并轮询（必须明确返回了 task_status）
                pass
            else:
                raise RuntimeError(
                    f"极速语音转写查询失败（code={code}） task_id={task_id}: "
                    f"{json.dumps(resp, ensure_ascii=False)[:1000]}"
                )

        # 以“是否返回 result”为准：部分账号/模式可能出现 task_status=4 但仍返回可用 result。
        # 这里不做兜底，而是严格使用服务端真实返回的数据：只要能解析出非空文本就视为成功。
        text = ""
        if (data.get("result") or {}).get("lattice") is not None:
            text = _parse_speed_text(resp)
            if text:
                return text

        # 0/1/2: 处理中；3: 完成；4: 失败（无结果）
        if status == 3:
            raise RuntimeError(
                f"极速语音转写返回空文本 task_id={task_id}: {json.dumps(resp, ensure_ascii=False)[:1000]}"
            )
        if status == 4:
            raise RuntimeError(
                f"极速语音转写任务失败（无可用结果） task_id={task_id}: {json.dumps(resp, ensure_ascii=False)[:1000]}"
            )

        # LangGraph dev/Studio 会对 time.sleep 报 BlockingError，这里用 Event.wait 替代。
        try:
            threading.Event().wait(max(0.0, float(poll_interval)))
        except Exception:
            pass
