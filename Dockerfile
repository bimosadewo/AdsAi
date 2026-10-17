# AdsAi — server online (Cloud Run). Lihat docs/SERVER-ONLINE.md.
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    ADSAI_AUDIT_LOG_DIR=/data/logs ADSAI_CLIENTS_FILE=/data/clients.yaml \
    ADSAI_STRATEGY_DIR=/data/strategi ADSAI_DOSSIER_DIR=/data/dossiers ADSAI_AUTH_STORE=/data/auth.json

WORKDIR /app
COPY pyproject.toml README.md NOTICE ./
COPY src ./src
# Root di dalam sandbox Cloud Run: volume Cloud Storage di /data dipasang dengan izin root.
RUN pip install . && mkdir -p /data

EXPOSE 8080
CMD ["adsai-server"]
