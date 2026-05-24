#!/usr/bin/env bash
set -euo pipefail

IMAGE_NAME="${IMAGE_NAME:-zyb_agent:latest}"
ARCHIVE_PATH="${1:-zyb_agent.tar.gz}"

docker save "${IMAGE_NAME}" | gzip -c > "${ARCHIVE_PATH}"
ls -lh "${ARCHIVE_PATH}"
