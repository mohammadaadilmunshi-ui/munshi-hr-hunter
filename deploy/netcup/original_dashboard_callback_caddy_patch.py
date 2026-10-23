"""Preview-only Caddy routing for the ORIGINAL MUNSHI dashboard domain.

Enables exactly the Tsenta OAuth callback and GET-only status bridge to
Hunter FastAPI, keeping Basic Auth on all other original dashboard paths.
Never rewrites Caddy in place or touches the private staging configuration.
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path

from deploy.netcup.tsenta_callback_caddy_patch import (
    PRODUCTION_HEADER,
    STAGING_HEADER,
)

CALLBACK = "/api/integrations/tsenta/oauth/callback"
BRIDGE = "/api/application-provider-status/by-application/*"


def patch_original_dashboard(contents: str) -> str:
    prod_pattern = re.compile(r"(?m)^" + re.escape(PRODUCTION_HEADER) + r"$")
    stage_pattern = re.compile(r"(?m)^" + re.escape(STAGING_HEADER) + r"$")
    original = list(prod_pattern.finditer(contents))
    staging = list(stage_pattern.finditer(contents))
    if len(original) != 1 or len(staging) != 1 or staging[0].start() >= original[0].start():
        raise ValueError("Unknown original dashboard Caddy layout.")
    before, production = contents[:original[0].start()], contents[original[0].start():]
    if CALLBACK in production or BRIDGE in production:
        raise ValueError("An application callback or bridge route already exists.")

    dashboard = re.compile(
        r"(?m)^(?P<indent>[ \t]*)@munshi_prod_dashboard\s+not\s+path\s+"
        r"/api/mail/inbound[ \t]*$"
    )
    matches = list(dashboard.finditer(production))
    if len(matches) != 1:
        raise ValueError("Unknown production Basic Auth matcher.")
    match = matches[0]
    indent = match.group("indent")
    routes = "\n".join(
        [
            indent + "@munshi_prod_tsenta_callback path " + CALLBACK,
            indent + "@munshi_prod_status_bridge {",
            indent + "    method GET",
            indent + "    path " + BRIDGE,
            indent + "}",
            indent + "@munshi_prod_dashboard not path /api/mail/inbound " + CALLBACK + " " + BRIDGE,
        ]
    )
    production = production[:match.start()] + routes + production[match.end():]
    upstream_re = re.compile(
        r"(?m)^(?P<indent>[ \t]*)reverse_proxy[ \t]+@munshi_prod_mail[ \t]+"
        r"(?P<upstream>[^\s#]+)[ \t]*$"
    )
    matched = list(upstream_re.finditer(production))
    if len(matched) != 1:
        raise ValueError("Original FastAPI upstream could not be proved.")
    proxy = matched[0]
    routes = "\n".join(
        [
            indent + "reverse_proxy @munshi_prod_tsenta_callback " + proxy.group("upstream"),
            indent + "reverse_proxy @munshi_prod_status_bridge " + proxy.group("upstream"),
        ]
    )
    production = (
        production[:proxy.end()] + "\n" + routes + production[proxy.end():]
    )
    if "basic_auth @munshi_prod_dashboard" not in production:
        raise ValueError("Original dashboard Basic Auth must remain protected.")
    updated = before + production
    if not updated.startswith(before) or updated[:staging[0].start()] != contents[:staging[0].start()]:
        raise AssertionError("Unexpected staging config change.")
    return updated


def main() -> None:
    parser = argparse.ArgumentParser(description="Preview production-target Caddy patch only")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.input.resolve() == args.output.resolve():
        raise SystemExit("Never overwrite the original Caddy source.")
    args.output.write_text(patch_original_dashboard(args.input.read_text(encoding="utf-8")), encoding="utf-8")


if __name__ == "__main__":
    main()
