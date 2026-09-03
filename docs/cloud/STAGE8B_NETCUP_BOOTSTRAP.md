# Stage 8B Netcup Bootstrap and Security Baseline

- Timestamp: 2026-08-31 (America/New_York)
- Branch: `feat/cloud-migration-foundation`
- Host: pending explicit input
- Architecture/OS target: Ubuntu 24.04 LTS x86_64
- Canonical workflow SHA-256: `501f144f35c5ae514a2c96004763232014ae78fa3a266f33a582f780cb22534f`
- Production Mac mutation count: `0`

`scripts/netcup/bootstrap_netcup_host.sh` requires an explicit host and private-key path, rejects localhost and Mac production paths, and performs a read-only forensic gate before installing anything. The gate proves Linux, Ubuntu 24.04, x86_64, at least 8 CPUs, AMD EPYC class/model evidence when the hypervisor exposes it, at least 14.5 GiB RAM, at least approximately 480 GB presented disk capacity, and at least 20 GB free on the root filesystem. It records the complete `lsblk` view, device type, model when exposed, size, `ROTA`, filesystem, root source/capacity/free space, network, hostname, and systemd state.

The Netcup RS 2000 G12 provider contract is AMD EPYC 9645, 8 dedicated cores, 16 GB DDR5 ECC, 512 GB NVMe SSD, and KVM virtualization. Inside this real KVM guest the storage is presented as `/dev/vda` with `ROTA=1`; there may be no device named `nvme*`. Guest device naming and the rotational flag describe the virtual presentation and are not reliable proof of the physical backing. Therefore neither `/dev/vda`, absence of `nvme*`, nor `ROTA=1` is a bootstrap failure. Storage performance is assessed after deployment by the bounded Stage 9 `fio` benchmark.

After the gate passes, the remote-only script sets the Netcup host timezone with `timedatectl set-timezone America/New_York` and requires `timedatectl show -p Timezone --value` to return exactly `America/New_York`. It then applies security updates; installs Docker Engine and Compose v2 from Docker's Ubuntu repository; installs conservative diagnostics; creates the `munshi` user and `/opt/munshi/{repo,data,logs,runtime,backups,secrets,reports}`; limits secret directory permissions; configures bounded Docker logs while preserving any existing daemon settings; enables Docker; configures UFW with deny-incoming/allow-outgoing and every currently configured SSH port; enables unattended upgrades; and creates a 2 GiB swapfile only when no swap exists and at least 20 GB is free. The timezone command runs over SSH on the Netcup host, never on the Mac.

## Stage 8B-B bootstrap command

Run only after replacing `<HOST>` with the provisioned Netcup address:

```bash
scripts/netcup/bootstrap_netcup_host.sh \
  --host <HOST> \
  --identity "$HOME/.ssh/munshi_netcup_ed25519" \
  --ssh-user root
```

The `munshi` user receives the dedicated public key and Docker access. Its passwordless sudo scope is limited to checking Docker's systemd state and rebooting the server for the authorized proof. Password authentication is not disabled. The script verifies key login as `munshi` before declaring success. Netcup console recovery remains available and operators must keep the initial session open until key login is confirmed.

Dedicated key fingerprint:

```text
SHA256:ZAsn333cd2gYOrfrAb1G2cqsxDC8mdC2xdLPF/1nCmc
```

The private key is `~/.ssh/munshi_netcup_ed25519`, mode 0600. Neither key is committed and SSH config is not edited.

## Exposure and access

Public exposure is SSH only. FastAPI 8000, Streamlit 8501, and n8n 5678 bind remote loopback; Ollama 11434 has no host mapping. Example tunnels:

```bash
ssh -i "$HOME/.ssh/munshi_netcup_ed25519" -L 8000:127.0.0.1:8000 munshi@<HOST>
ssh -i "$HOME/.ssh/munshi_netcup_ed25519" -L 8501:127.0.0.1:8501 munshi@<HOST>
ssh -i "$HOME/.ssh/munshi_netcup_ed25519" -L 5678:127.0.0.1:5678 munshi@<HOST>
```

Ollama must never be publicly forwarded. No `munshi.systems` DNS or production TLS certificate is created during shadow testing.
