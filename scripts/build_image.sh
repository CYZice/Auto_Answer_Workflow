#!/usr/bin/env bash
set -euo pipefail

IMAGE_NAME="${IMAGE_NAME:-zyb_agent:latest}"

docker build -t "${IMAGE_NAME}" .
