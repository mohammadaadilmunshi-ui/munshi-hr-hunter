ARG UIUX_BASE_IMAGE=munshi-uiux-20261019:candidate
FROM ${UIUX_BASE_IMAGE}
COPY scripts/uiux_stage.py /app/hunter/scripts/uiux_stage.py
COPY scripts/uiux_stage_guard.py /app/hunter/scripts/uiux_stage_guard.py
COPY scripts/phase17_staging_migrate.py /app/hunter/scripts/phase17_staging_migrate.py
ENTRYPOINT ["python", "scripts/uiux_stage.py"]
