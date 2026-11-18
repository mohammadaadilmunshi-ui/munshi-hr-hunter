"""Review-only Caddy staging callback patch.

Does NOT edit the source file or restart Caddy. Maintains an authenticated
dashboard and allows ONLY the exact OAuth callback path through staging.
The production site block is preserved byte-for-byte.
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path

CALLBACK = "/api/integrations/tsenta/oauth/callback"
STAGING_HEADER = "staging-dashboard.munshi.systems {"
PRODUCTION_HEADER = "dashboard.munshi.systems {"


def patch_caddy(contents: str) -> str:
    if len(re.findall(r"(?m)^" + re.escape(STAGING_HEADER) + r"$", contents)) != 1 or len(
        re.findall(r"(?m)^" + re.escape(PRODUCTION_HEADER) + r"$", contents)
    ) != 1:
        raise ValueError("Expected exactly one staging and one production Caddy site.")
    head = contents.index(STAGING_HEADER)
    prod = re.search(r'(?m)^' + re.escape(PRODUCTION_HEADER) + r'$', contents).start()
    if not head < prod:
        raise ValueError("Staging site must precede untouched production site.")
    before, staging, production = contents[:head], contents[head:prod], contents[prod:]
    if CALLBACK in staging:
        raise ValueError("Callback already appears; inspect before changing it.")

    expected = re.compile(
        r"(?m)^(?P<indent>[ \t]*)@munshi_dashboard\s+not\s+path\s+"
        r"/api/mail/inbound[ \t]*$"
    )
    items = list(expected.finditer(staging))
    if len(items) != 1:
        raise ValueError("Unexpected staging dashboard authentication matcher.")
    matcher = items[0]
    indent = matcher.group("indent")
    replacement = (
        indent + "@munshi_tsenta_callback path " + CALLBACK + "\n"
        + indent + "@munshi_dashboard not path "
        "/api/mail/inbound " + CALLBACK
    )
    staging = staging[: matcher.start()] + replacement + staging[matcher.end():]

    # The callback must proxy to exactly the same staging FastAPI upstream.
    # Reusing the existing mail webhook's host avoids guessing network names.
    mail_pattern = re.compile(
        r"(?m)^(?P<indent>[ \t]*)reverse_proxy[ \t]+@munshi_mail[ \t]+"
        r"(?P<target>[^\s#]+)[ \t]*$"
    )
    lines = list(mail_pattern.finditer(staging))
    if len(lines) != 1:
        raise ValueError("Cannot uniquely identify staging FastAPI upstream.")
    matched = lines[0]
    newline = (
        "\n" + matched.group("indent")
        + "reverse_proxy @munshi_tsenta_callback " + matched.group("target")
    )
    staging = staging[:matched.end()] + newline + staging[matched.end():]
    # Avoid reordering or editing the production site or its auth entries.
    if "basic_auth @munshi_dashboard" not in staging:
        raise ValueError("Expected staging dashboard Basic Auth protection.")
    if "@munshi_prod_dashboard" not in production:
        raise ValueError("Expected untouched production dashboard matcher.")
    result = before + staging + production
    if result[re.search(r"(?m)^" + re.escape(PRODUCTION_HEADER) + r"$",result).start():] != production:
        raise AssertionError("Production site was unexpectedly changed.")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate (do not apply) scoped Caddy patch")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.input.resolve() == args.output.resolve():
        raise SystemExit("Refusing to overwrite source Caddyfile.")
    original = args.input.read_text(encoding="utf-8")
    args.output.write_text(patch_caddy(original), encoding="utf-8")


if __name__ == "__main__":
    main()
