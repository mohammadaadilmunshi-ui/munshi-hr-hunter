#!/bin/sh
set -eu

cd /app/hunter
mkdir -p /app/hunter/data "${AADIL_HR_HUNTER_RUNTIME}" "${AADIL_HR_HUNTER_LOGS}"

exec python /app/hunter/docker/hunter-supervisor.py
