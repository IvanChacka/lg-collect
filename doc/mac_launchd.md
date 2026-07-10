# macOS 本地常驻 + 每天 11:00（北京时间）自动运行

本方案使用 macOS `launchd` 在本机常驻启动服务端，并在每天 11:00（以本机系统时区为准；若系统时区为 Asia/Shanghai 即北京时间）触发一次 `/run/daily`。

## 1) 安装依赖

```bash
pip install -r requirements.txt
```

## 2) 安装 launchd 任务（常驻服务 + 定时触发）

```bash
bash scripts/launchd/install_launchd.sh
```

安装后会创建两项 `LaunchAgents`：
- `com.hotcollect.server`：常驻 `uvicorn server:app --host 127.0.0.1 --port 8000`
- `com.hotcollect.daily_11_bj`：每天 11:00 执行 `python scripts/trigger_daily_run.py`，触发 `POST http://127.0.0.1:8000/run/daily`

日志文件：
- `.data/logs/server.stdout.log` / `.data/logs/server.stderr.log`
- `.data/logs/daily_11.stdout.log` / `.data/logs/daily_11.stderr.log`

## 3) 验证

确认服务是否活着：

```bash
curl -sS http://127.0.0.1:8000/graph/mermaid | head
```

手动触发一次运行（等价于定时任务做的事）：

```bash
curl -sS -X POST http://127.0.0.1:8000/run/daily -H 'Content-Type: application/json' -d '{}'
```

## 4) 修改工作流后如何“重新部署”

这里的“部署”是本机常驻服务（不是 LangSmith 托管部署）：

1) 修改代码（例如 `studio_graph.py` / `core/` / `nodes/` / `tools/`）
2) 如有新增依赖：`pip install -r requirements.txt`
3) 重启常驻服务：

```bash
launchctl kickstart -k "gui/${UID}/com.hotcollect.server"
```

（如果你改了定时触发脚本或 plist，也可以重启触发器）

```bash
launchctl kickstart -k "gui/${UID}/com.hotcollect.daily_11_bj"
```

## 5) 停用

```bash
launchctl unload "${HOME}/Library/LaunchAgents/com.hotcollect.server.plist"
launchctl unload "${HOME}/Library/LaunchAgents/com.hotcollect.daily_11_bj.plist"
```

