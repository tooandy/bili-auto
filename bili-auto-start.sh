#!/bin/bash
cd "$(dirname "$0")"

# 禁用代理
unset HTTP_PROXY HTTPS_PROXY ALL_PROXY http_proxy https_proxy all_proxy

# 结束之前的进程（防止旧进程状态异常）
SCRIPT_PID=$$
OLD_PIDS=$(pgrep -f "uv run python main.py" 2>/dev/null | grep -v "^$SCRIPT_PID$")
if [ -n "$OLD_PIDS" ]; then
    echo "[启动] 结束旧进程: $OLD_PIDS"
    echo "$OLD_PIDS" | xargs kill -9 2>/dev/null
    sleep 1
fi

# 使用完整路径执行 uv
exec /opt/homebrew/bin/uv run python main.py
