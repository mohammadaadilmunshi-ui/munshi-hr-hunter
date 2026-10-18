"""Exact production-only Caddy migration to 24-hour dashboard cookie auth.

Preserves staging verbatim and keeps all three published API exceptions.
No remote effects: outputs a separate candidate file.
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path

DOMAIN = "dashboard.munshi.systems"
PRODUCTION_HEADER = DOMAIN + " {"
EXEMPT_PATHS = (
    "/api/mail/inbound",
    "/api/integrations/tsenta/oauth/callback",
    "/api/application-provider-status/by-application/*",
)


def patch_caddy(contents: str) -> str:
    match = re.search(r"(?m)^dashboard\.munshi\.systems \{$", contents)
    if not match or len(re.findall(r"(?m)^dashboard\.munshi\.systems \{$", contents)) != 1:
        raise ValueError("Unexpected production domain layout")
    prefix, existing = contents[:match.start()], contents[match.start():]
    if not prefix or "staging-dashboard.munshi.systems {" not in prefix:
        raise ValueError("Staging block missing")
    if "forward_auth" in existing or "/_munshi-auth/login" in existing:
        raise ValueError("Device authentication already present; refuse replay")
    required = (
        "@munshi_prod_mail path /api/mail/inbound",
        "@munshi_prod_tsenta_callback path /api/integrations/tsenta/oauth/callback",
        "path /api/application-provider-status/by-application/*",
        "reverse_proxy @munshi_prod_mail 127.0.0.1:8000",
        "reverse_proxy @munshi_prod_tsenta_callback 127.0.0.1:8000",
        "reverse_proxy @munshi_prod_status_bridge 127.0.0.1:8000",
        "reverse_proxy @munshi_prod_dashboard 127.0.0.1:8501",
    )
    if any(existing.count(line) != 1 for line in required):
        raise ValueError("Current production exempt/proxy routing differs from expected")
    creds_match = re.search(
        r"(?m)^    basic_auth @munshi_prod_dashboard \{\n"
        r"(?P<credential>[^{}\r\n]+)\n    \}",
        existing,
    )
    if creds_match is None or existing.count("basic_auth @munshi_prod_dashboard {") != 1:
        raise ValueError("Unknown Basic Auth credential block")
    credentials = creds_match.group("credential")
    credential_tokens = credentials.strip().split()
    if len(credential_tokens) != 2 or not re.fullmatch(
        r"\$2[aby]\$\d\d\$[A-Za-z0-9./]+", credential_tokens[1]
    ):
        raise ValueError("Unrecognized existing password hash; refuse conversion")
    if not existing.rstrip().endswith("}") or existing.count("dashboard.munshi.systems") != 1:
        raise ValueError("Unexpected trailing production Caddy layout")

    # The same hashed password protects enrollment. It is never printed/logged.
    site = """dashboard.munshi.systems {
    encode zstd gzip

    @munshi_prod_mail path /api/mail/inbound
    @munshi_prod_tsenta_callback path /api/integrations/tsenta/oauth/callback
    @munshi_prod_status_bridge {
        method GET
        path /api/application-provider-status/by-application/*
    }
    @munshi_prod_device_login path /_munshi-auth/login
    @munshi_prod_device_logout path /_munshi-auth/logout
    @munshi_prod_stream path /_stcore/stream

    route {
        # For Streamlit upgrades, validate the cookie using ordinary HTTP first.
        handle @munshi_prod_stream {
            route {
                request_header -Upgrade
                request_header -Connection
                forward_auth 127.0.0.1:8000 {
                    uri /_munshi-auth/verify
                    header_up X-Munshi-Original-URI {uri}
                }
                request_header Upgrade websocket
                request_header Connection Upgrade
                reverse_proxy 127.0.0.1:8501
            }
        }
        handle @munshi_prod_mail {
            reverse_proxy 127.0.0.1:8000
        }
        handle @munshi_prod_tsenta_callback {
            reverse_proxy 127.0.0.1:8000
        }
        handle @munshi_prod_status_bridge {
            reverse_proxy 127.0.0.1:8000
        }
        handle @munshi_prod_device_login {
            basic_auth {
__CREDENTIAL__
            }
            reverse_proxy 127.0.0.1:8000 {
                header_up X-Munshi-Auth-User {http.auth.user.id}
            }
        }
        handle @munshi_prod_device_logout {
            reverse_proxy 127.0.0.1:8000
        }
        handle {
            forward_auth 127.0.0.1:8000 {
                uri /_munshi-auth/verify
                header_up X-Munshi-Original-URI {uri}
            }
            reverse_proxy 127.0.0.1:8501
        }
    }
}
""".replace("__CREDENTIAL__", "    " + credentials)
    return prefix + site


def main() -> None:
    parser = argparse.ArgumentParser(description="Preview production Caddy 24h auth only")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.input.resolve() == args.output.resolve():
        raise SystemExit("Output must not overwrite Caddy source")
    args.output.write_text(patch_caddy(args.input.read_text()))


if __name__ == "__main__":
    main()
