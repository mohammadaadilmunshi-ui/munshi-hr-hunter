"""Regressions for the SMTP2GO sender setup form without needing a Streamlit session."""

from pathlib import Path

def test_smtp_sender_input_accepts_verified_domain_address():
    import ast
    import re

    # No Streamlit session or vault access needed to verify input validation.
    module = ast.parse(
        (Path(__file__).resolve().parents[1] / "app" / "integration_credentials_ui_v1.py").read_text()
    )
    selected = [
        node for node in module.body
        if isinstance(node, (ast.FunctionDef, ast.Assign))
        and (
            (isinstance(node, ast.FunctionDef) and node.name in {
                "_validate_sender", "_validate_api_key", "_validated_smtp_fields"
            })
            or (isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id == "_EMAIL"
                for target in node.targets
            ))
        )
    ]
    namespace = {"re": re}
    exec(compile(ast.Module(body=selected, type_ignores=[]), "<smtp-validation-test>", "exec"), namespace)
    validate_sender = namespace["_validate_sender"]
    validate_fields = namespace["_validated_smtp_fields"]
    assert validate_sender(" Notifications@Munshi.Systems ") == "notifications@munshi.systems"
    assert validate_fields("", "notifications@munshi.systems", existing_api_key=True) == (
        None, "notifications@munshi.systems"
    )
    assert validate_fields("valid-api-key-with-20-characters", "notifications@munshi.systems") == (
        "valid-api-key-with-20-characters", "notifications@munshi.systems"
    )
    import pytest
    with pytest.raises(ValueError, match="Enter the SMTP2GO API key"):
        validate_fields("", "notifications@munshi.systems")
    with pytest.raises(ValueError, match="valid email"):
        validate_fields("valid-api-key-with-20-characters", "not-an-email")
    with pytest.raises(ValueError, match="valid email"):
        validate_sender("evil\\@munshi.systems")
