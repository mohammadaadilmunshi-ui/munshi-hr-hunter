# Ollama Migration Policy

Ollama is currently installed/running on the Mac and Ollama nodes exist in the
canonical n8n workflow.

The Ollama branch is gated by conditions including:

- `config.ats_use_local_ollama === true`
- `ats_local_weave_required === true`

Ollama is therefore preserved as existing source behavior but is NOT considered
a mandatory cloud dependency until live execution evidence proves that the
production path requires it.

Do not remove the branch from the Mac during the initial migration.
Do not provision Ollama in cloud solely because the code exists.
