# Gmail OAuth activation

The Product UI includes a read-only Gmail OAuth callback, but this branch does
not change the production edge or provision credentials.  Activation later
requires the owner to set `MUNSHI_GMAIL_CLIENT_ID`,
`MUNSHI_GMAIL_CLIENT_SECRET`, `MUNSHI_GMAIL_REDIRECT_URI`, and a 32-byte
base64url `MUNSHI_VAULT_KEY` only in the server runtime.

Register the redirect URI as the HTTPS endpoint
`https://<authenticated-hunter-host>/api/gmail/oauth/callback`. Configure the
existing HTTPS reverse proxy with an exact, GET-only callback route to Hunter
FastAPI that preserves the query string; if the normal browser authentication
would challenge Google, exempt only this exact callback route. Do not publish
FastAPI port 8000. The callback itself cannot use the normal browser API-secret
header, so it fails closed through a cryptographically random, single-use,
ten-minute state and PKCE verifier encrypted in the vault.

No sync happens until the connected user explicitly chooses **Sync now**.  The
integration requests Gmail's read-only scope and has no send capability.
