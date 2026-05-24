#!/usr/bin/env bash
set -euo pipefail

ARCHIVE_PATH="${1:-zyb_agent.tar.gz}"
REMOTE_HOST="${REMOTE_HOST:-root@139.196.90.36}"
REMOTE_DIR="${REMOTE_DIR:-/root/zybagent}"
SSH_PASSWORD="${SSH_PASSWORD:?SSH_PASSWORD is required}"

sshpass -p "${SSH_PASSWORD}" scp -o StrictHostKeyChecking=accept-new \
  "${ARCHIVE_PATH}" "${REMOTE_HOST}:${REMOTE_DIR}/$(basename "${ARCHIVE_PATH}")"
