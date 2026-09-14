const MUNSHI_MAIL_DOMAIN = "@mail.munshi.systems";
const MAX_RAW_BYTES = 10 * 1024 * 1024;
const MIN_SIGNING_SECRET_BYTES = 32;
const encoder = new TextEncoder();

function bytesToHex(buffer) {
  return Array.from(new Uint8Array(buffer))
    .map((value) => value.toString(16).padStart(2, "0"))
    .join("");
}

function requireSafeValue(name, value, maximumBytes) {
  const normalized = String(value || "").trim();
  if (!normalized) {
    throw new Error(`${name} is required.`);
  }
  if (/[\r\n\0]/.test(normalized)) {
    throw new Error(`${name} contains invalid control characters.`);
  }
  if (encoder.encode(normalized).byteLength > maximumBytes) {
    throw new Error(`${name} is too large.`);
  }
  return normalized;
}

function requireEndpoint(env) {
  const value = requireSafeValue(
    "MUNSHI_MAIL_ENDPOINT",
    env.MUNSHI_MAIL_ENDPOINT,
    2048,
  );
  let endpoint;
  try {
    endpoint = new URL(value);
  } catch {
    throw new Error("MUNSHI_MAIL_ENDPOINT is not a valid URL.");
  }
  if (endpoint.protocol !== "https:") {
    throw new Error("MUNSHI_MAIL_ENDPOINT must use HTTPS.");
  }
  return endpoint.toString();
}

function requireSigningSecret(env) {
  const secret = String(env.MUNSHI_MAIL_SIGNING_SECRET || "");
  if (encoder.encode(secret).byteLength < MIN_SIGNING_SECRET_BYTES) {
    throw new Error(
      "MUNSHI_MAIL_SIGNING_SECRET must be at least 32 bytes.",
    );
  }
  return secret;
}

async function sha256Hex(buffer) {
  return bytesToHex(
    await crypto.subtle.digest("SHA-256", buffer),
  );
}

async function hmacSha256Hex(secret, material) {
  const key = await crypto.subtle.importKey(
    "raw",
    encoder.encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  return bytesToHex(
    await crypto.subtle.sign(
      "HMAC",
      key,
      encoder.encode(material),
    ),
  );
}

function canonicalMaterial({
  timestamp,
  recipient,
  sender,
  messageId,
  bodySha256,
}) {
  return [
    "v1",
    timestamp,
    recipient,
    sender,
    messageId,
    bodySha256,
  ].join("\n");
}

function permanentBackendRejection(status) {
  return new Set([400, 404, 409, 413, 415, 422]).has(status);
}

export default {
  async email(message, env) {
    const recipient = String(message.to || "").trim().toLowerCase();
    const sender = String(message.from || "").trim();
    const messageId = String(
      message.headers.get("message-id") || "",
    ).trim();

    // This Worker processes only MUNSHI automation aliases.
    // Personal/root-domain mail must never enter this path.
    if (!recipient.endsWith(MUNSHI_MAIL_DOMAIN)) {
      message.setReject(
        "Recipient is outside the MUNSHI automation domain.",
      );
      return;
    }

    // Fail closed on configuration errors. There is intentionally no personal
    // inbox forwarding fallback: automation mail either reaches MUNSHI
    // securely or remains failed/rejected in the routing path.
    const endpoint = requireEndpoint(env);
    const signingSecret = requireSigningSecret(env);

    let safeRecipient;
    let safeSender;
    let safeMessageId;
    try {
      safeRecipient = requireSafeValue(
        "recipient",
        recipient,
        320,
      ).toLowerCase();
      safeSender = requireSafeValue(
        "sender",
        sender,
        320,
      );
      safeMessageId = requireSafeValue(
        "Message-ID",
        messageId,
        998,
      );
    } catch (error) {
      console.warn("MUNSHI inbound metadata rejected", {
        recipient,
        messageId: messageId || null,
        error:
          error instanceof Error
            ? error.message
            : "invalid metadata",
      });
      message.setReject("MUNSHI could not accept this message.");
      return;
    }

    if (
      !Number.isFinite(message.rawSize)
      || message.rawSize <= 0
      || message.rawSize > MAX_RAW_BYTES
    ) {
      message.setReject(
        "Message exceeds the MUNSHI inbound size limit.",
      );
      return;
    }

    const raw = await new Response(message.raw).arrayBuffer();
    if (raw.byteLength === 0 || raw.byteLength > MAX_RAW_BYTES) {
      message.setReject(
        "Message exceeds the MUNSHI inbound size limit.",
      );
      return;
    }

    const timestamp = Math.floor(Date.now() / 1000).toString();
    const bodySha256 = await sha256Hex(raw);
    const material = canonicalMaterial({
      timestamp,
      recipient: safeRecipient,
      sender: safeSender,
      messageId: safeMessageId,
      bodySha256,
    });
    const signature = await hmacSha256Hex(
      signingSecret,
      material,
    );

    let response;
    try {
      response = await fetch(endpoint, {
        method: "POST",
        headers: {
          "Content-Type": "message/rfc822",
          "X-Munshi-Version": "v1",
          "X-Munshi-Timestamp": timestamp,
          "X-Munshi-Recipient": safeRecipient,
          "X-Munshi-Sender": safeSender,
          "X-Munshi-Message-ID": safeMessageId,
          "X-Munshi-Signature": `v1=${signature}`,
        },
        body: raw,
      });
    } catch (error) {
      console.error("MUNSHI inbound HTTPS request failed", {
        recipient: safeRecipient,
        messageId: safeMessageId,
        error:
          error instanceof Error
            ? error.message
            : "network failure",
      });
      throw error;
    }

    if (response.ok) {
      return;
    }

    if (permanentBackendRejection(response.status)) {
      console.warn("MUNSHI backend rejected inbound mail", {
        recipient: safeRecipient,
        messageId: safeMessageId,
        status: response.status,
      });
      message.setReject(
        "MUNSHI could not accept this automation message.",
      );
      return;
    }

    // 401/403 means signing/config drift; 429 and 5xx are service failures.
    // Throw rather than forwarding mail anywhere else.
    console.error("MUNSHI backend temporarily unavailable", {
      recipient: safeRecipient,
      messageId: safeMessageId,
      status: response.status,
    });
    throw new Error(
      `MUNSHI inbound endpoint returned ${response.status}`,
    );
  },
};
