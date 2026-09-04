# Staging access bridge

Generated: 2026-09-04T14:55:41Z

This file contains **no secrets**.

- GitHub repository: `mohammadaadilmunshi-ui/munshi-hr-hunter`
- GitHub CLI auth: **PASS**
- Netcup host: `159.195.244.16`
- TCP/22 from normal macOS Terminal: **PASS**
- SSH target/alias: `munshi@159.195.244.16`
- BatchMode SSH: **PASS**
- Expected staging path: `/home/munshi/munshi-staging-v1`
- Expected staging wrapper: `/opt/munshi/bin/deploy-staging-release`
- Staging domain: `https://staging-dashboard.munshi.systems/`
- Protected production domain: `https://dashboard.munshi.systems/`

## Safety state

No deployment, push, migration, container restart, DNS/Caddy modification,
database mutation, n8n modification, or production mutation was performed.

## Important Codex note

Passing host-side GitHub/SSH checks does **not** remove Codex
`workspace-write` sandbox restrictions on `.git` or outbound SSH. A
separate staging-only deployment session with explicitly sufficient sandbox
permissions is still required if Codex itself is to perform Git/SSH deployment.
