# Stage 6 Source Remediation

This stage versions the exact custom HR scoring adapter and closes the source
portability boundary identified by the Stage 6A diagnostics.

- The full HackerRank `interviewstreet/hiring-agent` repository is not needed
  for this custom scoring path. The modified upstream Jinja template is not
  needed either.
- The adapter is standard-library-only, versioned at
  `integrations/hr_agent/n8n_hr_score.py`, and remains a subprocess-driven CLI.
- Ollama is required for a successful HR Agent score. The direct legacy n8n
  Ollama lane remains separately optional and gated by `OLLAMA_ENABLED` /
  `OLLAMA_REQUIRED`; those flags do not disable or satisfy the adapter's
  `OLLAMA_BASE_URL` dependency.
- The canonical n8n workflow remains immutable. Deployment endpoints are
  substituted only into an explicitly generated workflow copy.
- Dice will require Playwright Chromium in the future Hunter container.
- Docker has not started, production state has not migrated, and n8n
  credentials/encryption-key migration remains a later stage.
- x86_64 Linux is proven; ARM64 Linux remains unproven.

No live service, database, credential, browser profile, provider API, Telegram
integration, or Ollama runtime was accessed by this source-only stage.
