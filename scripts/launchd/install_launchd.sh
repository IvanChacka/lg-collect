#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="/Users/mima0000/Desktop/hot_collect"
PLIST_DIR="${HOME}/Library/LaunchAgents"

mkdir -p "${PLIST_DIR}"
mkdir -p "${REPO_DIR}/.data/logs"

cp "${REPO_DIR}/scripts/launchd/com.hotcollect.server.plist" "${PLIST_DIR}/com.hotcollect.server.plist"
cp "${REPO_DIR}/scripts/launchd/com.hotcollect.daily_11_bj.plist" "${PLIST_DIR}/com.hotcollect.daily_11_bj.plist"

launchctl unload "${PLIST_DIR}/com.hotcollect.server.plist" >/dev/null 2>&1 || true
launchctl unload "${PLIST_DIR}/com.hotcollect.daily_11_bj.plist" >/dev/null 2>&1 || true

launchctl load "${PLIST_DIR}/com.hotcollect.server.plist"
launchctl load "${PLIST_DIR}/com.hotcollect.daily_11_bj.plist"

echo "Installed and loaded:"
echo "- com.hotcollect.server (uvicorn server:app on 127.0.0.1:8000)"
echo "- com.hotcollect.daily_11_bj (triggers POST /run/daily daily at 11:00 local time)"

