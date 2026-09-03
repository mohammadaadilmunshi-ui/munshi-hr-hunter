## Summary

Describe the change in practical terms.

## Scope

- [ ] Source/UI only
- [ ] Adapter/provider behavior
- [ ] n8n/workflow contract
- [ ] Docker/Compose/runtime
- [ ] Deployment/infrastructure
- [ ] Documentation only

## Production impact

Describe whether this can affect Hunter, Telegram, scheduler/coordinator, n8n, Ollama, Streamlit, FastAPI, databases, or cloud deployment.

## Safety checklist

- [ ] No live database, WAL/SHM, production `.env`, secret, token, credential, private key, or user-private artifact is committed.
- [ ] No `docker compose down -v`, destructive volume command, or database replacement is introduced.
- [ ] No automatic production deploy on push/PR/schedule is introduced.
- [ ] Mac production authority is not re-enabled.
- [ ] Public port exposure is unchanged or explicitly reviewed.
- [ ] If Hunter can be recreated, the proven five-layer Compose contract is preserved.
- [ ] n8n and Ollama are not recreated as a normal Hunter deployment side effect.
- [ ] Rollback is documented for any runtime-impacting change.
- [ ] Netcup deployment does not depend on unauthenticated outbound GitHub fetches.
- [ ] GitHub → Netcup deployment transport preserves exact Git SHA/branch ancestry.
- [ ] Successful deployment keeps production attached to the approved source branch at the exact deployed SHA.
- [ ] Deploy-key installation uses an exact approved source SHA and is rollback-safe.

## Verification

List the exact checks/tests run and their results.

- [ ] Linux Compatibility
- [ ] Docker Foundation
- [ ] Repository Safety Guard
- [ ] Deployment Transport Guard where deployment files are present
- [ ] Focused pytest / regression tests
- [ ] Shell syntax where applicable
- [ ] JobSpy runtime/image contract where applicable

## Git / production synchronization

- [ ] This branch is based on the current approved source lineage.
- [ ] There is no known GitHub ↔ Netcup production history gap, or the PR is explicitly marked blocked/draft until resolved.

## Deployment

Production deployment requested by this PR?

- [ ] No
- [ ] Yes — exact approved SHA and rollback evidence will be required separately
