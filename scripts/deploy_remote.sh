#!/usr/bin/env bash
set -euo pipefail

ARCHIVE_NAME="${1:-zyb_agent.tar.gz}"
REMOTE_HOST="${REMOTE_HOST:-root@139.196.90.36}"
REMOTE_DIR="${REMOTE_DIR:-/root/zybagent}"
SSH_PASSWORD="${SSH_PASSWORD:?SSH_PASSWORD is required}"

sshpass -p "${SSH_PASSWORD}" ssh -o StrictHostKeyChecking=accept-new "${REMOTE_HOST}" "\
  set -e; \
  cd ${REMOTE_DIR}; \
  gunzip -c ${ARCHIVE_NAME} | docker load; \
  docker compose up -d --force-recreate zyb_agent; \
  docker image prune -f; \
  docker ps --filter name=^/zyb_agent\$ --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}'; \
  docker exec zyb_agent sh -lc 'which pandoc && pandoc --version | head -n 2' \
"
