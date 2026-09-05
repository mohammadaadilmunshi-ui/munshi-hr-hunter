FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/hunter \
    PLAYWRIGHT_BROWSERS_PATH=0 \
    FASTAPI_HOST=0.0.0.0 \
    STREAMLIT_HOST=0.0.0.0 \
    AADIL_HR_HUNTER_PROJECT=/app/hunter \
    AADIL_HR_HUNTER_RUNTIME=/app/hunter/.runtime \
    AADIL_HR_HUNTER_LOGS=/app/hunter/logs

RUN apt-get update \
    && apt-get install -y --no-install-recommends chromium ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app/hunter

COPY requirements.lock.txt ./requirements.lock.txt
RUN python -m pip install --no-cache-dir -r requirements.lock.txt

COPY tools/runners/jobspy_runner.py ./tools/runners/jobspy_runner.py
COPY app ./app
COPY config ./config
COPY integrations ./integrations
COPY migrations ./migrations
COPY scripts/render_n8n_deployment_workflow.py ./scripts/render_n8n_deployment_workflow.py
COPY scripts/validate_container_environment_contract.py ./scripts/validate_container_environment_contract.py

# One-time staging bootstrap payload. These are the only host executables that
# this temporary release is allowed to install. A cleanup release immediately
# removes the privileged mount/root override from the staging Hunter.
COPY deploy/netcup/github_deploy_gateway.sh ./bootstrap/github_deploy_gateway.sh
COPY deploy/netcup/apply_dashboard_device_auth_edge.sh ./bootstrap/apply_dashboard_device_auth_edge.sh
COPY deploy/netcup/verify_staging_runtime_contract.sh ./bootstrap/verify_staging_runtime_contract.sh
RUN chmod 0555 \
    ./bootstrap/github_deploy_gateway.sh \
    ./bootstrap/apply_dashboard_device_auth_edge.sh \
    ./bootstrap/verify_staging_runtime_contract.sh

RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin hunter \
    && mkdir -p /app/hunter/data /app/hunter/.runtime /app/hunter/logs \
    && chown -R hunter:hunter /app/hunter

USER hunter

COPY --chown=hunter:hunter docker/hunter-entrypoint.sh ./docker/hunter-entrypoint.sh
COPY --chown=hunter:hunter docker/hunter-supervisor.py ./docker/hunter-supervisor.py
RUN chmod 0555 ./docker/hunter-entrypoint.sh

EXPOSE 8000 8501
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)"

ENTRYPOINT ["/app/hunter/docker/hunter-entrypoint.sh"]
