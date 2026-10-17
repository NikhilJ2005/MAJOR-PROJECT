"""Example plugin (extra_files): deploy configs so a generated app can go to Render, Fly.io or Railway.

The generated app already has a Dockerfile (migrations on start, non-root, health check), so every
config just points the host at it. No host is special: pick any, or several.
"""

import json

DESCRIPTION = "Adds render.yaml, fly.toml and railway.json so the app deploys to Render, Fly.io or Railway"


def extra_files(spec) -> dict[str, str]:
    name = spec.project_name.replace("_", "-")
    secrets = ["SECRET_KEY"] if spec.auth or spec.stack == "django" else []
    render_env = "".join(f"      - key: {key}\n        generateValue: true\n" for key in secrets)
    if spec.auth:
        render_env += "      - key: ADMIN_EMAILS\n        sync: false\n"
    render = f"""# Render Blueprint: New -> Blueprint -> pick this repository.
services:
  - type: web
    name: {name}
    runtime: docker
    healthCheckPath: /health
    envVars:
      - key: ENV
        value: production
      - key: DATABASE_URL
        fromDatabase:
          name: {name}-db
          property: connectionString
{render_env}
databases:
  - name: {name}-db
    plan: basic-256mb
"""
    fly = f"""# Fly.io: fly launch --copy-config, then fly postgres create && fly postgres attach <db>
#         fly secrets set {' '.join(f'{k}=...' for k in secrets) or 'ENV=production'}
app = "{name}"

[build]
  dockerfile = "Dockerfile"

[env]
  ENV = "production"

[http_service]
  internal_port = 8000
  force_https = true
  auto_stop_machines = "stop"
  min_machines_running = 0

  [[http_service.checks]]
    interval = "30s"
    timeout = "5s"
    method = "GET"
    path = "/health"
"""
    railway = {
        "$schema": "https://railway.com/railway.schema.json",
        "build": {"builder": "DOCKERFILE", "dockerfilePath": "Dockerfile"},
        "deploy": {"healthcheckPath": "/health", "restartPolicyType": "ON_FAILURE"},
    }
    return {"render.yaml": render, "fly.toml": fly, "railway.json": json.dumps(railway, indent=2) + "\n"}
