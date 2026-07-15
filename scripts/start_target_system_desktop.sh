#!/bin/sh
set -eu

: "${TARGET_SYSTEM_VNC_PASSWORD:?请设置 TARGET_SYSTEM_VNC_PASSWORD 后再启动 Linux 浏览器交付服务。}"

export DISPLAY="${DISPLAY:-:99}"
SCREEN_SIZE="${TARGET_SYSTEM_SCREEN_SIZE:-1440x960x24}"
PASSWORD_FILE="${TARGET_SYSTEM_VNC_PASSWORD_FILE:-/app/data/target-system-vnc.pass}"

mkdir -p "$(dirname "$PASSWORD_FILE")"
x11vnc -storepasswd "$TARGET_SYSTEM_VNC_PASSWORD" "$PASSWORD_FILE" >/dev/null

Xvfb "$DISPLAY" -screen 0 "$SCREEN_SIZE" -nolisten tcp &
openbox-session &
x11vnc -display "$DISPLAY" -rfbauth "$PASSWORD_FILE" -forever -shared -localhost -rfbport 5900 &
websockify --web /usr/share/novnc 6080 localhost:5900 &

exec python scripts/target_system_delivery_worker.py
