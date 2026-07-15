#!/usr/bin/env bash
set -euo pipefail

: "${SSH_HOST:?请设置 SSH_HOST}"
: "${SSH_USER:?请设置 SSH_USER}"
: "${SSH_PASSWORD:?请设置 SSH_PASSWORD}"

if [[ $# -ne 2 ]]; then
  echo "用法: $0 <本地镜像文件> <远端路径>" >&2
  exit 2
fi

sshpass -p "${SSH_PASSWORD}" scp \
  -o StrictHostKeyChecking=accept-new \
  "$1" "${SSH_USER}@${SSH_HOST}:$2"
