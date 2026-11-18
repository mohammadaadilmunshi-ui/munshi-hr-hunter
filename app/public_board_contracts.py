"""Reviewed public employer contracts; discovery output cannot approve itself."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Mapping, Any

from app.discovery_policy import require_free_provider

CONTRACTS_PATH = Path(__file__).resolve().parents[1] / "config/public_board_contracts.json"


def approved_public_contract(board: Mapping[str, Any], *, format: str) -> dict:
    for contract in json.loads(CONTRACTS_PATH.read_text())["boards"]:
        if (contract.get("format") == format
                and str(contract["company_name"]).casefold() == str(board.get("company_name", "")).casefold()
                and contract["board_url"] == board.get("board_url")
                and contract.get("evidence_url")):
            require_free_provider(contract["provider"], cost_mode=contract.get("cost_mode", "unknown"))
            return dict(contract)
    raise ValueError("NEEDS_COMPANY_BOARD: no independently verified public feed contract")
