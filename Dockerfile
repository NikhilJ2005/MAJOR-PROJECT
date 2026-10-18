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
# kubectl, for one-click deploys of generated apps (app/deploy.py); the checksum is verified.
ARG KUBECTL_VERSION=v1.31.4
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates && rm -rf /var/lib/apt/lists/* \
    && curl -fsSLo /usr/local/bin/kubectl https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/amd64/kubectl \
    && echo "$(curl -fsSL https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/amd64/kubectl.sha256)  /usr/local/bin/kubectl" | sha256sum -c - \
    && chmod +x /usr/local/bin/kubectl
# The Kubernetes MCP server (read-only), which the deploy diagnoser uses to look into the cluster itself.
ARG KUBERNETES_MCP_VERSION=0.0.67
RUN curl -fsSLo /usr/local/bin/kubernetes-mcp-server \
      https://github.com/containers/kubernetes-mcp-server/releases/download/v${KUBERNETES_MCP_VERSION}/kubernetes-mcp-server-linux-amd64 \
    && chmod +x /usr/local/bin/kubernetes-mcp-server && kubernetes-mcp-server --version
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
