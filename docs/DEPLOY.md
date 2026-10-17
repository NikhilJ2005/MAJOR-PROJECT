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

## Railway
1. Deploy from the repository. `railway.json` uses the Dockerfile and `/api/health`.
2. Add a **Postgres** service and set `PLATFORM_DATABASE_URL=${{Postgres.DATABASE_URL}}`.
3. Either keep a volume at `/data`, or set the `S3_*` variables (Cloudflare R2 has free egress).

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

## Backups
- **Postgres:** use the host's automatic backups (Railway, Render and Fly all offer them), or run `pg_dump` nightly.
- **Bucket:** turn on object versioning, or a lifecycle rule. Zips can always be rebuilt from a project's history.

## Monitoring
- **Health:** `GET /api/health`.
- **Metrics:** `GET /api/metrics` (Prometheus text). It reports runs by status, build time, repairs, tokens and cost. Admins send their login token.
- **Logs:** one JSON object per line, each with a `request_id`, so any host's log viewer can search them.
- **Errors:** set `SENTRY_DSN` to send crashes to Sentry.
