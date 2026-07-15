#!/usr/bin/env bash
set -euo pipefail

: "${SSH_HOST:?请设置 SSH_HOST}"
: "${SSH_USER:?请设置 SSH_USER}"
: "${SSH_PASSWORD:?请设置 SSH_PASSWORD}"

sshpass -p "${SSH_PASSWORD}" ssh \
  -o StrictHostKeyChecking=accept-new \
  "${SSH_USER}@${SSH_HOST}" "$@"
