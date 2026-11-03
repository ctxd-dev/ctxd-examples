FROM node:22-bookworm-slim AS frontend
WORKDIR /build
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

FROM python:3.12-slim-bookworm AS base
RUN apt-get update && apt-get install -y --no-install-recommends git openssl ca-certificates \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN useradd --uid 10001 --create-home app \
    && mkdir /data /connection /app-state && chown app:app /data /connection /app-state
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 SSL_CERT_FILE=/connection/ca-bundle.pem
COPY context.py ctxd_context.py agent.py gemini_agent.py limits.py app.py ./
USER app
FROM base AS web
COPY --from=frontend /build/dist ./web/dist
EXPOSE 8840
CMD ["python", "app.py"]
FROM base AS data
COPY producer.py data_service.py docs_sync.py ./
EXPOSE 8850
CMD ["python", "data_service.py", "start"]
