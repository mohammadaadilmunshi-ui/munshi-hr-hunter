# Cloud Migration Baseline

Current source environment:

- macOS
- Apple Silicon ARM64
- FastAPI
- Streamlit
- n8n
- Telegram listener
- randomized source scheduler
- unified hourly coordinator
- Hunter SQLite persistent state
- n8n SQLite persistent state

Canonical n8n workflow candidate:

`L1u2xZkgFpi7KEuv`

The first cloud migration must preserve behavior before introducing larger
architecture changes such as PostgreSQL, Redis, distributed workers or queue
mode.

The Mac remains production authority until cloud shadow parity is proven.
