#!/bin/sh
set -eu

cd /app/hunter
mkdir -p /app/hunter/data "${AADIL_HR_HUNTER_RUNTIME}" "${AADIL_HR_HUNTER_LOGS}"

if [ "${MUNSHI_REMOTE_EDGE_GATEWAY_BOOTSTRAP:-0}" = "1" ]; then
  [ "$(id -u)" = "0" ] || { echo "remote edge gateway bootstrap requires container root" >&2; exit 80; }
  [ -d /host-bin ] || { echo "remote edge gateway bootstrap host-bin mount missing" >&2; exit 81; }
  [ -f /app/hunter/bootstrap/github_deploy_gateway.sh ] || { echo "gateway bootstrap payload missing" >&2; exit 82; }
  [ -f /app/hunter/bootstrap/apply_dashboard_device_auth_edge.sh ] || { echo "edge helper bootstrap payload missing" >&2; exit 83; }
  [ -f /app/hunter/bootstrap/verify_staging_runtime_contract.sh ] || { echo "staging verifier bootstrap payload missing" >&2; exit 84; }

  install -o 0 -g 0 -m 0755 /app/hunter/bootstrap/github_deploy_gateway.sh /host-bin/.github-deploy-gateway.new
  install -o 0 -g 0 -m 0755 /app/hunter/bootstrap/apply_dashboard_device_auth_edge.sh /host-bin/.apply-dashboard-device-auth-edge.new
  install -o 0 -g 0 -m 0755 /app/hunter/bootstrap/verify_staging_runtime_contract.sh /host-bin/.verify-staging-runtime-contract.new

  mv -f /host-bin/.github-deploy-gateway.new /host-bin/github-deploy-gateway
  mv -f /host-bin/.apply-dashboard-device-auth-edge.new /host-bin/apply-dashboard-device-auth-edge
  mv -f /host-bin/.verify-staging-runtime-contract.new /host-bin/verify-staging-runtime-contract

  sh -n /host-bin/github-deploy-gateway
  bash -n /host-bin/apply-dashboard-device-auth-edge
  bash -n /host-bin/verify-staging-runtime-contract
  grep -Fq '/opt/munshi/bin/apply-dashboard-device-auth-edge' /host-bin/github-deploy-gateway
  grep -Fq 'RESULT=DASHBOARD_DEVICE_AUTH_EDGE_PASS' /host-bin/apply-dashboard-device-auth-edge
  grep -Fq 'TRUSTED_DEVICE_SESSION' /host-bin/verify-staging-runtime-contract
  echo "REMOTE_EDGE_GATEWAY_BOOTSTRAP=PASS"
  echo "REMOTE_EDGE_STAGING_VERIFIER_BOOTSTRAP=PASS"

  exec python - <<'PY'
import grp
import os
import pwd
import sys

user = pwd.getpwnam("hunter")
group = grp.getgrnam("hunter")
os.setgroups([])
os.setgid(group.gr_gid)
os.setuid(user.pw_uid)
os.chdir("/app/hunter")
os.execve(sys.executable, [sys.executable, "/app/hunter/docker/hunter-supervisor.py"], os.environ.copy())
PY
fi

exec python /app/hunter/docker/hunter-supervisor.py
