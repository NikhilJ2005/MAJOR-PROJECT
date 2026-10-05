# One image, one Railway service: FastAPI serves the API and the exported Next.js UI.

# ---- frontend: static export -------------------------------------------------
FROM node:22-slim AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ---- backend -------------------------------------------------------------------
FROM python:3.11-slim
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DATA_DIR=/data \
    FRONTEND_DIR=/srv/frontend \
    SANDBOX=local \
    SANDBOX_USER=sandbox
WORKDIR /srv/backend
COPY backend/pyproject.toml ./
RUN pip install --no-cache-dir uv && uv pip install --system --no-cache -r pyproject.toml
COPY backend/ ./
COPY --from=web /web/out /srv/frontend

# Generated code runs as `sandbox`, never as the server's user, so it cannot read
# the server's environment (API key) via /proc. The server keeps root so it can
# switch users and write to a Railway volume mounted at /data.
RUN useradd --system --no-create-home --uid 10001 sandbox && mkdir -p /data
EXPOSE 8000
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='*'"]
