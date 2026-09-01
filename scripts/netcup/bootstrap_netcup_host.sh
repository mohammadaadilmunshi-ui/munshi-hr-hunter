#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=_common.sh
source "$SCRIPT_DIR/_common.sh"

host=""
identity=""
ssh_user="root"
public_key=""

usage() {
  printf 'Usage: %s --host HOST --identity PRIVATE_KEY [--ssh-user USER] [--public-key FILE]\n' "$0"
}

while (($#)); do
  case "$1" in
    --host) host=${2:-}; shift 2 ;;
    --identity) identity=${2:-}; shift 2 ;;
    --ssh-user) ssh_user=${2:-}; shift 2 ;;
    --public-key) public_key=${2:-}; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) netcup_die "unknown argument: $1" ;;
  esac
done

netcup_validate_host "$host"
netcup_validate_identity "$identity"
public_key=${public_key:-"$identity.pub"}
[[ -f "$public_key" ]] || netcup_die "public key does not exist: $public_key"
netcup_require_command ssh
netcup_require_command scp
netcup_verify_remote_identity "$host" "$identity" "$ssh_user"

forensic_file=$(mktemp "${TMPDIR:-/tmp}/munshi-netcup-forensic.XXXXXX")
key_file=$(mktemp "${TMPDIR:-/tmp}/munshi-netcup-key.XXXXXX")
cleanup() { rm -f "$forensic_file" "$key_file"; }
trap cleanup EXIT
cp "$public_key" "$key_file"
chmod 600 "$key_file"

netcup_ssh "$host" "$identity" "$ssh_user" 'set -eu
printf "FORENSIC_VERSION=1\n"
printf "UNAME_S=%s\n" "$(uname -s)"
printf "UNAME_M=%s\n" "$(uname -m)"
. /etc/os-release
printf "OS_ID=%s\nOS_VERSION_ID=%s\nOS_PRETTY=%s\n" "$ID" "$VERSION_ID" "$PRETTY_NAME"
printf "HOSTNAME=%s\n" "$(hostname -f 2>/dev/null || hostname)"
printf "CPU_COUNT=%s\n" "$(getconf _NPROCESSORS_ONLN)"
printf "CPU_MODEL=%s\n" "$(lscpu | awk -F: '\''/Model name/{sub(/^[[:space:]]+/,"",$2); print $2; exit}'\'')"
printf "MEM_KIB=%s\n" "$(awk '\''/MemTotal/{print $2}'\'' /proc/meminfo)"
printf "ROOT_DISK_BYTES=%s\n" "$(lsblk -bndo SIZE,TYPE | awk '\''$2=="disk"{if($1>m)m=$1}END{print m+0}'\'')"
printf "NVME_COUNT=%s\n" "$(lsblk -dno NAME | awk '\''/^nvme/{n++}END{print n+0}'\'')"
printf "SYSTEMD_STATE=%s\n" "$(systemctl is-system-running 2>/dev/null || true)"
uname -a
lscpu
free -h
lsblk -e7 -o NAME,MODEL,SERIAL,SIZE,TYPE,FSTYPE,MOUNTPOINTS
df -hT
ip -brief addr
ip route
hostnamectl
if command -v curl >/dev/null 2>&1; then
  printf "OUTBOUND_IP=%s\n" "$(curl -fsS --max-time 5 https://ifconfig.me/ip 2>/dev/null || true)"
  printf "GEOLOCATION_SIGNAL=%s\n" "$(curl -fsS --max-time 5 https://ipapi.co/country_name/ 2>/dev/null || true)"
fi
' | tee "$forensic_file"

value() { sed -n "s/^$1=//p" "$forensic_file" | head -n1; }
[[ "$(value UNAME_S)" == "Linux" ]] || netcup_die "RESULT: NO_GO_NETCUP_HARDWARE_MISMATCH (not Linux)"
[[ "$(value UNAME_M)" == "x86_64" ]] || netcup_die "RESULT: NO_GO_NETCUP_HARDWARE_MISMATCH (not x86_64)"
[[ "$(value OS_ID)" == "ubuntu" && "$(value OS_VERSION_ID)" == "24.04" ]] || netcup_die "unsupported OS; safely reprovision Ubuntu 24.04 LTS x86_64"
(( $(value CPU_COUNT) >= 8 )) || netcup_die "RESULT: NO_GO_NETCUP_HARDWARE_MISMATCH (fewer than 8 CPUs)"
(( $(value MEM_KIB) >= 14500000 )) || netcup_die "RESULT: NO_GO_NETCUP_HARDWARE_MISMATCH (less than approximately 16 GB RAM)"
(( $(value ROOT_DISK_BYTES) >= 480000000000 )) || netcup_die "RESULT: NO_GO_NETCUP_HARDWARE_MISMATCH (disk below expected class)"
(( $(value NVME_COUNT) >= 1 )) || netcup_die "RESULT: NO_GO_NETCUP_HARDWARE_MISMATCH (no NVMe device presented)"

netcup_scp "$host" "$identity" "$ssh_user" "$key_file" /tmp/munshi_netcup_authorized_key.pub

netcup_ssh "$host" "$identity" "$ssh_user" 'sudo -n bash -s' <<'REMOTE'
set -euo pipefail
[[ "$(uname -s)" == Linux && "$(uname -m)" == x86_64 ]] || exit 70
. /etc/os-release
[[ "$ID" == ubuntu && "$VERSION_ID" == 24.04 ]] || exit 71

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get -y upgrade
apt-get install -y ca-certificates curl gnupg git jq openssl ufw unattended-upgrades sqlite3 fio sysbench nvme-cli time

install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc
arch=$(dpkg --print-architecture)
. /etc/os-release
printf 'Types: deb\nURIs: https://download.docker.com/linux/ubuntu\nSuites: %s\nComponents: stable\nArchitectures: %s\nSigned-By: /etc/apt/keyrings/docker.asc\n' "$VERSION_CODENAME" "$arch" > /etc/apt/sources.list.d/docker.sources
apt-get update
apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

if ! id munshi >/dev/null 2>&1; then
  useradd --create-home --shell /bin/bash munshi
fi
usermod -aG docker munshi
usermod -aG systemd-journal munshi
cat > /etc/sudoers.d/munshi-cloud-shadow <<'SUDOERS'
munshi ALL=(root) NOPASSWD: /usr/bin/systemctl reboot, /usr/bin/systemctl is-active docker, /usr/bin/systemctl is-enabled docker
SUDOERS
chmod 0440 /etc/sudoers.d/munshi-cloud-shadow
visudo -cf /etc/sudoers.d/munshi-cloud-shadow
install -d -m 0700 -o munshi -g munshi /home/munshi/.ssh
touch /home/munshi/.ssh/authorized_keys
chmod 0600 /home/munshi/.ssh/authorized_keys
if ! grep -qxF "$(cat /tmp/munshi_netcup_authorized_key.pub)" /home/munshi/.ssh/authorized_keys; then
  cat /tmp/munshi_netcup_authorized_key.pub >> /home/munshi/.ssh/authorized_keys
fi
chown -R munshi:munshi /home/munshi/.ssh
rm -f /tmp/munshi_netcup_authorized_key.pub

for path in repo data logs runtime backups secrets reports; do
  install -d -m 0750 -o munshi -g munshi "/opt/munshi/$path"
done
chmod 0700 /opt/munshi/secrets

install -d -m 0755 /etc/docker
if [[ -f /etc/docker/daemon.json ]]; then
  cp -a /etc/docker/daemon.json "/etc/docker/daemon.json.pre-munshi.$(date -u +%Y%m%dT%H%M%SZ)"
fi
python3 - <<'PY'
import json
from pathlib import Path
path = Path('/etc/docker/daemon.json')
data = json.loads(path.read_text()) if path.exists() and path.read_text().strip() else {}
data['log-driver'] = 'json-file'
opts = dict(data.get('log-opts', {}))
opts.update({'max-size': '10m', 'max-file': '5'})
data['log-opts'] = opts
path.write_text(json.dumps(data, indent=2) + '\n')
PY

systemctl enable --now docker
systemctl restart docker

mapfile -t ssh_ports < <(sshd -T 2>/dev/null | awk '$1=="port"{print $2}' | sort -un)
((${#ssh_ports[@]})) || ssh_ports=(22)
ufw default deny incoming
ufw default allow outgoing
for port in "${ssh_ports[@]}"; do ufw allow "$port/tcp"; done
ufw --force enable

dpkg-reconfigure -f noninteractive unattended-upgrades
if [[ $(swapon --show --noheadings | wc -l) -eq 0 ]] && [[ $(df -BG --output=avail / | tail -n1 | tr -dc '0-9') -ge 20 ]]; then
  fallocate -l 2G /swapfile
  chmod 0600 /swapfile
  mkswap /swapfile
  swapon /swapfile
  grep -q '^/swapfile ' /etc/fstab || printf '/swapfile none swap sw 0 0\n' >> /etc/fstab
fi

report="/opt/munshi/reports/bootstrap_$(date -u +%Y%m%dT%H%M%SZ).txt"
{
  printf 'timestamp=%s\n' "$(date -u +%FT%TZ)"
  printf 'hostname=%s\n' "$(hostname -f 2>/dev/null || hostname)"
  printf 'architecture=%s\n' "$(uname -m)"
  printf 'os=%s\n' "$PRETTY_NAME"
  printf 'cpu_count=%s\n' "$(nproc)"
  printf 'cpu_model=%s\n' "$(lscpu | awk -F: '/Model name/{sub(/^[[:space:]]+/,"",$2); print $2; exit}')"
  printf 'docker=%s\n' "$(docker --version)"
  printf 'compose=%s\n' "$(docker compose version)"
  printf 'firewall=%s\n' "$(ufw status | head -n1)"
  printf 'production_mac_mutations=0\n'
} > "$report"
chown munshi:munshi "$report"
printf 'BOOTSTRAP_REPORT=%s\n' "$report"
REMOTE

forensic_name="netcup_preflight_$(date -u +%Y%m%dT%H%M%SZ).txt"
netcup_scp "$host" "$identity" "$ssh_user" "$forensic_file" "/tmp/$forensic_name"
netcup_ssh "$host" "$identity" "$ssh_user" "sudo -n install -o munshi -g munshi -m 0640 /tmp/$forensic_name /opt/munshi/reports/$forensic_name && rm -f /tmp/$forensic_name"

netcup_ssh "$host" "$identity" munshi 'set -eu; test "$(uname -s)" = Linux; test "$(uname -m)" = x86_64; docker version >/dev/null; docker compose version >/dev/null'
printf 'FORENSIC_REPORT=/opt/munshi/reports/%s\n' "$forensic_name"
printf 'RESULT: GO_NETCUP_BOOTSTRAP_COMPLETE\n'
printf 'PRODUCTION_MAC_MUTATIONS: 0\n'
