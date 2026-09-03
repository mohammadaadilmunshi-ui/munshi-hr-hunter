from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ADAPTER = (ROOT / "integrations/hr_agent/n8n_hr_score.py").read_text(encoding="utf-8")
PROXY = (ROOT / "app/hr_agent_proxy.py").read_text(encoding="utf-8")


def test_adapter_is_versioned_and_portable():
    assert (ROOT / "integrations/hr_agent/n8n_hr_score.py").is_file()
    assert "/Users/aadil" not in ADAPTER
    assert "Documents/AI-Tools/hiring-agent" not in ADAPTER
    assert "interviewstreet" not in ADAPTER and "hiring-agent" not in ADAPTER
    tree = ast.parse(ADAPTER)
    imported_modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_modules.update(
                alias.name.split(".")[0]
                for alias in node.names
            )
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_modules.add(node.module.split(".")[0])

    assert imported_modules <= {
        "__future__",
        "json",
        "os",
        "re",
        "sys",
        "urllib",
        "typing",
    }
    for value in ("OLLAMA_BASE_URL", "HR_AGENT_OLLAMA_MODEL", "HR_AGENT_OLLAMA_TIMEOUT_SECONDS", "gemma3:4b", "600", "strict Human Resources resume evaluator"):
        assert value in ADAPTER
    assert "timeout=OLLAMA_TIMEOUT_SECONDS" in ADAPTER


def test_proxy_uses_repository_adapter_and_preserves_contract():
    assert "/api/hr-agent/score" in PROXY
    assert '"stdout"' in PROXY and '"stderr"' in PROXY and '"exitCode"' in PROXY and '"proxy_status"' in PROXY
    assert "Path(sys.executable)" in PROXY
    assert 'ROOT_DIR / "integrations" / "hr_agent" / "n8n_hr_score.py"' in PROXY
    assert "Documents/AI-Tools/hiring-agent" not in PROXY and ".venv" not in PROXY
    assert '"HR_AGENT_PROCESS_TIMEOUT_SECONDS", 240' in PROXY
    assert "subprocess.run" in PROXY and "input=decoded_payload" in PROXY
