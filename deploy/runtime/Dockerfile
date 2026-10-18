# The runtime for apps VibeStack deploys to Kubernetes (DEPLOY_RUNTIME_IMAGE).
# It holds Python and every package a generated FastAPI, Flask or Django app uses, and nothing else:
# each app's code is downloaded by its pods at start (plugins/kubernetes.py), so no image is built per app.
#   docker build -t ghcr.io/your-org/vibestack-runtime:1 deploy/runtime && docker push ghcr.io/your-org/vibestack-runtime:1
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
# Keep in step with agent/stacks.py (Stack.dependencies); tests/test_deploy.py checks it.
RUN pip install --no-cache-dir \
    "fastapi>=0.110" "uvicorn[standard]>=0.28" "flask>=3.0" "gunicorn>=22.0" "django>=5.1" \
    "sqlalchemy>=2.0" "alembic>=1.13" "pydantic>=2.6" "psycopg[binary]>=3.1" "pyjwt>=2.8" "redis>=5.0"
RUN useradd --uid 10001 --no-create-home app && mkdir /app && chown app /app
USER 10001
WORKDIR /app
