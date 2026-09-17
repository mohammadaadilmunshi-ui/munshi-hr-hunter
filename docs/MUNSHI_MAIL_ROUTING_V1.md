# MUNSHI Mail Routing V1

## Safety contract

This pipeline receives application/ATS automation mail only. It has no authority to approve a review, issue a submit command, operate an employer browser, or submit an application. Inbound receipt stores and correlates mail only.

Personal Gmail is not part of the automation path.

## Flow

```text
mail.munshi.systems
  -> Cloudflare Email Routing
  -> munshi-mail-router
  -> signed HTTPS message/rfc822
  -> POST /api/mail/inbound
  -> HMAC + timestamp validation
  -> exact ACTIVE managed-alias lookup
  -> bounded MIME parsing
  -> Message-ID deduplication
  -> owner storage
  -> optional application correlation when exactly one live WAITING
     verification request matches the alias and sender domain
```

Zero matching verification requests stores the message owner-scoped with no application key. More than one matching request is marked `AMBIGUOUS` and also stores no application key. The receiver never guesses.

## HMAC protocol V1

The Worker sends the raw RFC822 message as the HTTPS request body with `Content-Type: message/rfc822`.

Required headers:

- `X-Munshi-Version: v1`
- `X-Munshi-Timestamp: <unix seconds>`
- `X-Munshi-Recipient: <normalized envelope recipient>`
- `X-Munshi-Sender: <envelope sender>`
- `X-Munshi-Message-ID: <RFC822 Message-ID>`
- `X-Munshi-Signature: v1=<lowercase HMAC-SHA256 hex>`

The canonical signed bytes are UTF-8:

```text
v1\n
<timestamp>\n
<recipient>\n
<sender>\n
<message-id>\n
<sha256(raw RFC822 bytes)>
```

The timestamp acceptance window is five minutes. The signing secret must be at least 32 bytes. Signature comparison is constant-time in Hunter.

Subject, RFC822 headers, text, HTML, MIME structure, and attachment metadata come from the signed raw RFC822 body rather than trusting duplicated unsigned HTTP metadata.

## Limits

Hunter intentionally uses limits below Cloudflare's platform maximum:

- raw RFC822: 10 MiB
- attachments: 20
- one decoded attachment: 5 MiB
- total decoded attachments: 8 MiB
- stored plain-text body: 512 KiB
- stored HTML body: 512 KiB

Attachment binaries are not persisted by this receiver. MUNSHI stores attachment metadata, decoded size, MIME type, filename/content ID, and SHA-256 digest.

## Runtime configuration

Hunter server:

```bash
MUNSHI_APPLICATION_MAIL_DOMAIN=mail.munshi.systems
MUNSHI_APPLICATION_MAIL_RELAY_ENABLED=false
MUNSHI_MAIL_SIGNING_SECRET=<strong random secret>
```

Keep `MUNSHI_APPLICATION_MAIL_RELAY_ENABLED=false` until the backend and Worker are deployed and ready for controlled direct testing.

Cloudflare Worker secrets/configuration:

```text
MUNSHI_MAIL_ENDPOINT
MUNSHI_MAIL_SIGNING_SECRET
```

`MUNSHI_MAIL_ENDPOINT` must be the public HTTPS Hunter endpoint ending in `/api/mail/inbound`. The same `MUNSHI_MAIL_SIGNING_SECRET` value must be provisioned securely on Hunter and the Worker. Never commit either runtime value.

A suitable secret can be generated locally with:

```bash
openssl rand -hex 32
```

If managing the Worker with Wrangler, provision values interactively rather than putting them in source:

```bash
npx wrangler secret put MUNSHI_MAIL_ENDPOINT
npx wrangler secret put MUNSHI_MAIL_SIGNING_SECRET
```

## Controlled rollout

Do not enable the `mail.munshi.systems` catch-all as the first test.

1. Deploy the Hunter code with the final-submit feature flag unchanged/off.
2. Provision the matching Hunter signing secret.
3. Deploy `infra/cloudflare/munshi-mail-router.js` with its two Worker secrets.
4. Set `MUNSHI_APPLICATION_MAIL_DOMAIN=mail.munshi.systems` on Hunter.
5. Enable the managed-mail relay only for the controlled test environment so a real ACTIVE MUNSHI alias exists.
6. Route one literal test alias to `munshi-mail-router` and send an external test email to it.
7. Verify the HTTP delivery is accepted and exactly one `inbound_mail_messages` row exists.
8. Retry the same message/event and verify it is idempotent.
9. Verify an unknown alias is rejected.
10. Verify a deliberately invalid signature/stale event is rejected when testing the HTTP endpoint directly.
11. Verify a matching verification request remains `WAITING` after receipt; inbound receipt alone must not advance verification or application submission state.
12. Verify there is no `message.forward()` call and no personal mailbox fallback in the Worker.
13. Only after all checks pass, change the Cloudflare `mail.munshi.systems` catch-all action to Worker `munshi-mail-router`.
14. Re-run a real mail test through the catch-all.

The root/personal `@munshi.systems` mail path is separate and must not be pointed at this Worker.

## Expected backend outcomes

- `200`: accepted or idempotent duplicate
- `400`: malformed signed metadata or RFC822 mismatch
- `401`: stale/bad signature or unsupported protocol version
- `404`: unknown, RESERVED, or SUSPENDED managed alias
- `409`: Message-ID reused with different message bytes
- `413`: raw/body/attachment limit exceeded
- `415`: request is not `message/rfc822`
- `503`: signing secret is missing/too weak

The Worker treats malformed/unknown/oversized mail as a permanent reject and treats authentication/configuration/service failures as failures rather than forwarding them to another mailbox.
