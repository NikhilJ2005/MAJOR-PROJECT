# Deploying VibeStack anywhere

VibeStack is one Docker image (`Dockerfile`): FastAPI serves the API and the web UI, and builds
generated apps in a sandbox. Every host runs the same image with the same environment variables.
Two things decide where data lives, and both can be anything:

| Data | Default | Production |
|---|---|---|
| Accounts, projects, versions, agent checkpoints | SQLite in `DATA_DIR` | **Postgres**: `PLATFORM_DATABASE_URL` (any Postgres 14+) |
| Generated zips | files in `DATA_DIR` | **S3-compatible bucket**: `S3_BUCKET`, and also `S3_ENDPOINT_URL`, `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY` and `S3_REGION` |

With Postgres and a bucket configured, the server keeps nothing on its own disk, so it can move
between hosts. Builds interrupted by a restart or redeploy resume from their last checkpoint.

## Settings for every host
| Variable | Value |
|---|---|
| `LLM_PROVIDER`, `GEMINI_API_KEY` | the AI provider (see `.env.example`) |
| `SECRET_KEY` | a long random string that signs logins; keep it stable |
| `ADMIN_EMAILS` | your email(s) |
| `ACCESS_CODE` | an invite code (optional) |
| `PLATFORM_DATABASE_URL` | Postgres connection URL |
| `S3_*` | object storage for zips |
| `PUBLIC_URL` | the public address, e.g. `https://vibestack.example.com` (used in links the MCP server returns) |
| `SENTRY_DSN` | error reports to Sentry (optional) |
| `RUN_QUEUE`, `REDIS_URL`, `WORKER_CONCURRENCY` | build workers (optional, see below) |

## Build workers (Redis)
By default the web server builds every run itself, `MAX_CONCURRENT_RUNS` at a time. With
`RUN_QUEUE=redis` and `REDIS_URL` set, it only **queues** runs, and worker processes
(`python -m app.worker`, same image) build them, `WORKER_CONCURRENCY` at a time each:

- **More builds at once:** add worker replicas. Builds no longer compete with the web server for CPU.
- **Survives restarts:** a worker refreshes a claim on each run it builds. When a worker dies or is
  redeployed, its claims expire after 30 s and another worker continues those runs from their last
  checkpoint. On SIGTERM a worker takes no new runs and finishes its current ones for up to
  `WORKER_DRAIN_SECONDS` (default 25).
- **Any web server can stream any run:** events live in Redis, so the browser may reach any replica.
- **Needs Postgres** (`PLATFORM_DATABASE_URL`): web and workers share runs and checkpoints through it.

Workers make the platform handle **many builds at once**. A **single** build gets faster from the
agents themselves (parallel model calls, a thinking level per job, the syntax self-check and parallel
validation gates), not from more machines.

## Railway
1. Deploy from the repository. `railway.json` uses the Dockerfile and `/api/health`.
2. Add a **Postgres** service and set `PLATFORM_DATABASE_URL=${{Postgres.DATABASE_URL}}`.
3. Either keep a volume at `/data`, or set the `S3_*` variables (Cloudflare R2 has free egress).
4. Optional build workers:
   1. Add a **Redis** service.
   2. Set `RUN_QUEUE=redis` and `REDIS_URL=${{Redis.REDIS_URL}}` on the web service.
   3. Add a second service from the same repository, with the start command `python -m app.worker`
      and the same variables as the web service (shared variables make this easy).
   4. To build more at once, raise that service's replicas.

## Render
1. **New → Blueprint**, then pick the repository. `render.yaml` creates the web service and a Postgres.
2. Fill in the secrets it asks for (`GEMINI_API_KEY`, `S3_*`). `SECRET_KEY` is generated for you.

## Fly.io
```bash
fly launch --copy-config --no-deploy
fly postgres create && fly postgres attach <db-name>
fly secrets set PLATFORM_DATABASE_URL=<url from attach> GEMINI_API_KEY=... SECRET_KEY=... S3_BUCKET=... \
    S3_ENDPOINT_URL=... S3_ACCESS_KEY_ID=... S3_SECRET_ACCESS_KEY=...
fly deploy
```

## Any VM (no platform at all)
```bash
cp .env.example .env                       # AI key, SECRET_KEY, ADMIN_EMAILS
docker compose --profile full up -d --build
```
This starts VibeStack, Postgres and MinIO (S3-compatible) on one machine. Put a reverse proxy with HTTPS
in front (Caddy: `reverse_proxy localhost:8000`), and set `S3_PUBLIC_ENDPOINT_URL` to the address where
browsers reach MinIO.

## Kubernetes
`deploy/k8s/` holds plain manifests with kustomize:
- a namespace, a ConfigMap and an example Secret
- the web Deployment (2 replicas), its Service, a PodDisruptionBudget and an ingress-nginx Ingress (SSE-friendly)
- the worker Deployment, with a HorizontalPodAutoscaler from 1 to 10 replicas on CPU
- a small Redis

Postgres and the bucket are managed services you point the Secret at.

```bash
# 1. Build and push the image, then set its name in deploy/k8s/kustomization.yaml (images:)
docker build -t ghcr.io/your-org/vibestack:latest . && docker push ghcr.io/your-org/vibestack:latest
# 2. Fill in deploy/k8s/secret.yaml (or create the Secret yourself) and the host in ingress.yaml
kubectl apply -k deploy/k8s
kubectl -n vibestack get pods
```

Live previews run inside the web pod that started them, so the Ingress keeps each browser on one web
pod (cookie affinity). CI renders the manifests and validates them with kubeconform.

## Backups
- **Postgres:** use the host's automatic backups (Railway, Render and Fly all offer them), or run `pg_dump` nightly.
- **Bucket:** turn on object versioning, or a lifecycle rule. Zips can always be rebuilt from a project's history.

## Monitoring
- **Health:** `GET /api/health`.
- **Metrics:** `GET /api/metrics` (Prometheus text). It reports runs by status, build time, repairs, tokens and cost. Admins send their login token.
- **Logs:** one JSON object per line, each with a `request_id`, so any host's log viewer can search them.
- **Errors:** set `SENTRY_DSN` to send crashes to Sentry.
