from __future__ import annotations

import re
from typing import Any


STOP_WORDS = {
    "a",
    "an",
    "and",
    "for",
    "of",
    "the",
    "to",
}

ABBREVIATIONS = (
    (r"\bhrbp\b", "human resources business partner"),
    (r"\bhris\b", "human resources information systems"),
    (r"\bhr\b", "human resources"),
    (r"\bta\b", "talent acquisition"),
)


def normalize_role_text(value: Any) -> str:
    text = str(value or "").strip().lower()

    # Normalize common HR title abbreviations symmetrically for both
    # discovered titles and dashboard-driven target roles.
    for pattern, replacement in ABBREVIATIONS:
        text = re.sub(pattern, replacement, text)

    text = re.sub(
        r"[^a-z0-9+#]+",
        " ",
        text,
    )

    return re.sub(
        r"\s+",
        " ",
        text,
    ).strip()


def meaningful_tokens(value: Any) -> list[str]:
    return [
        token
        for token in normalize_role_text(value).split()
        if token not in STOP_WORDS
    ]


def _ordered_token_span_match(
    title_tokens: list[str],
    role_tokens: list[str],
) -> bool:
    """Narrow ordered-token fallback to avoid bag-of-words false positives."""
    if len(role_tokens) < 2 or len(title_tokens) < len(role_tokens):
        return False

    max_span = len(role_tokens) + 1
    for start, token in enumerate(title_tokens):
        if token != role_tokens[0]:
            continue
        role_index = 1
        end = start
        while role_index < len(role_tokens) and end + 1 < len(title_tokens):
            end += 1
            if title_tokens[end] == role_tokens[role_index]:
                role_index += 1
            if end - start + 1 > max_span:
                break
        if role_index == len(role_tokens) and end - start + 1 <= max_span:
            return True
    return False


def match_target_role(
    title: Any,
    target_roles: list[str],
) -> tuple[bool, str | None, str]:
    normalized_title = normalize_role_text(title)
    title_tokens = meaningful_tokens(title)

    if not normalized_title:
        return False, None, "missing_job_title"

    best_match: str | None = None
    best_reason: str | None = None
    best_strength = -1

    for target_role in target_roles:
        normalized_role = normalize_role_text(target_role)
        role_tokens = meaningful_tokens(target_role)

        if not normalized_role or not role_tokens:
            continue

        if normalized_role in normalized_title:
            strength = len(role_tokens) + 100
            reason = "target_role_phrase_match"
        elif _ordered_token_span_match(title_tokens, role_tokens):
            strength = len(role_tokens)
            reason = "target_role_ordered_token_match"
        else:
            continue

        if strength > best_strength:
            best_strength = strength
            best_match = target_role
            best_reason = reason

    if best_match is None:
        return False, None, "title_not_in_dashboard_target_roles"

    return True, best_match, best_reason or "dashboard_target_role_match"
