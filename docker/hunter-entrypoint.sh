#!/bin/sh
set -eu

cd /app/hunter
mkdir -p /app/hunter/data "${AADIL_HR_HUNTER_RUNTIME}" "${AADIL_HR_HUNTER_LOGS}"

if [ "${MUNSHI_PRODUCTION_STAGING_RECOVERY_BOOTSTRAP:-0}" = "1" ]; then
  [ "$(id -u)" = "0" ] || { echo "production staging recovery bootstrap requires container root" >&2; exit 80; }
  [ -d /host-bin ] || { echo "production staging recovery host-bin mount missing" >&2; exit 81; }
  [ -f /app/hunter/bootstrap/github_deploy_gateway.sh ] || { echo "gateway recovery payload missing" >&2; exit 82; }
  [ -f /app/hunter/bootstrap/recover_staging_auth_bootstrap.sh ] || { echo "staging recovery payload missing" >&2; exit 83; }

  install -o 0 -g 0 -m 0755 /app/hunter/bootstrap/github_deploy_gateway.sh /host-bin/.github-deploy-gateway.new
  install -o 0 -g 0 -m 0755 /app/hunter/bootstrap/recover_staging_auth_bootstrap.sh /host-bin/.recover-staging-auth-bootstrap.new
  mv -f /host-bin/.github-deploy-gateway.new /host-bin/github-deploy-gateway
  mv -f /host-bin/.recover-staging-auth-bootstrap.new /host-bin/recover-staging-auth-bootstrap

  bash -n /host-bin/github-deploy-gateway
  bash -n /host-bin/recover-staging-auth-bootstrap
  grep -Fq '/opt/munshi/bin/recover-staging-auth-bootstrap' /host-bin/github-deploy-gateway
  grep -Fq 'RESULT=STAGING_AUTH_BOOTSTRAP_RECOVERY_PASS' /host-bin/recover-staging-auth-bootstrap
  echo "PRODUCTION_STAGING_RECOVERY_GATEWAY_BOOTSTRAP=PASS"

  exec python - <<'PY'
import os
import pwd
import sys

user = pwd.getpwnam("hunter")
os.setgroups([])
os.setgid(user.pw_gid)
os.setuid(user.pw_uid)
os.chdir("/app/hunter")
os.execve(sys.executable, [sys.executable, "/app/hunter/docker/hunter-supervisor.py"], os.environ.copy())
PY
fi

exec python /app/hunter/docker/hunter-supervisor.py
