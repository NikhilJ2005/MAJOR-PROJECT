"""Plugin (extra_files): Kubernetes manifests for the generated app.

The same function, k8s_manifests, also drives VibeStack's one-click deploy (app/deploy.py), so the
manifests in the downloaded zip are the ones VibeStack itself applies. Two ways to get the code into
the cluster:
- image mode (the download): build the project's own Dockerfile and push it to your registry.
- code mode (one-click deploy): a generic runtime image (deploy/runtime/Dockerfile) downloads the
  version's code at start, so no image is built per app.
"""

import yaml

DESCRIPTION = "Adds k8s/ manifests: the app, its Postgres, a migration Job, an Ingress, quotas and network rules"
WHEN = "the user mentions Kubernetes, k8s, Helm, containers at scale, clusters or autoscaling"
KEYWORDS = ("kubernetes", "k8s", "helm", "cluster", "autoscal")

PORT = 8000
# Pulls the version's code into /app (code mode). Standard library only, so the runtime image needs nothing extra.
FETCH_CODE = (
    "import io, os, urllib.request, zipfile; "
    "data = urllib.request.urlopen(os.environ['CODE_URL'], timeout=60).read(); "
    "zipfile.ZipFile(io.BytesIO(data)).extractall('/app')"
)
MIGRATE = {
    "fastapi": "alembic upgrade head",
    "flask": "alembic upgrade head",
    "django": "python manage.py migrate --noinput --run-syncdb",
}
SERVE = {
    "fastapi": f"uvicorn app.main:app --host 0.0.0.0 --port {PORT} --proxy-headers --forwarded-allow-ips='*'",
    "flask": f"gunicorn --bind 0.0.0.0:{PORT} --workers 2 --access-logfile - app.main:app",
    "django": f"gunicorn --bind 0.0.0.0:{PORT} --workers 2 --access-logfile - app.wsgi:application",
}


def k8s_manifests(spec, *, name, namespace=None, image, code_url=None, host="app.example.com", ingress_class="nginx",
                  tls_issuer="", storage_class="", secrets=None, version="1"):
    """Every object the app needs, as dicts in apply order. secrets=None leaves the Secret to you (download)."""
    app_labels = {"app": name, "app.kubernetes.io/managed-by": "vibestack"}
    db_labels = {"app": f"{name}-db", "app.kubernetes.io/managed-by": "vibestack"}
    meta = (lambda n, labels=app_labels: {"name": n, "labels": labels, **({"namespace": namespace} if namespace else {})})
    env_secret = f"{name}-env"
    code_volumes, code_init, code_mount = [], [], []
    if code_url:
        code_volumes = [{"name": "code", "emptyDir": {}}]
        code_mount = [{"name": "code", "mountPath": "/app"}]
        code_init = [{
            "name": "fetch-code", "image": image, "command": ["python", "-c", FETCH_CODE],
            "env": [{"name": "CODE_URL", "value": code_url}], "volumeMounts": code_mount,
            "resources": {"requests": {"cpu": "50m", "memory": "64Mi"}, "limits": {"memory": "256Mi"}},
        }]
    container = {
        "image": image, "workingDir": "/app" if code_url else None,
        "envFrom": [{"secretRef": {"name": env_secret}}],
        "env": [{"name": "ENV", "value": "production"}, {"name": "PYTHONUNBUFFERED", "value": "1"}],
        "volumeMounts": code_mount,
        "securityContext": {"allowPrivilegeEscalation": False, "runAsNonRoot": True, "runAsUser": 10001},
    }
    container = {k: v for k, v in container.items() if v not in (None, [])}
    pod_security = {"runAsNonRoot": True, "runAsUser": 10001, "fsGroup": 10001}

    objects = []
    if namespace:
        objects.append({"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": namespace, "labels": app_labels}})
        objects.append({"apiVersion": "v1", "kind": "ResourceQuota", "metadata": meta(f"{name}-quota"), "spec": {"hard": {
            "requests.cpu": "2", "requests.memory": "3Gi", "limits.memory": "4Gi", "pods": "12", "persistentvolumeclaims": "2"}}})
        objects.append({"apiVersion": "v1", "kind": "LimitRange", "metadata": meta(f"{name}-limits"), "spec": {"limits": [{
            "type": "Container", "default": {"memory": "512Mi"}, "defaultRequest": {"cpu": "100m", "memory": "128Mi"}}]}})
    if secrets is not None:
        objects.append({"apiVersion": "v1", "kind": "Secret", "metadata": meta(env_secret), "type": "Opaque",
                        "stringData": secrets})
    db_volume = {"metadata": {"name": "data"}, "spec": {
        "accessModes": ["ReadWriteOnce"], "resources": {"requests": {"storage": "1Gi"}},
        **({"storageClassName": storage_class} if storage_class else {})}}
    objects += [
        {"apiVersion": "apps/v1", "kind": "StatefulSet", "metadata": meta(f"{name}-db", db_labels), "spec": {
            "serviceName": f"{name}-db", "replicas": 1, "selector": {"matchLabels": {"app": f"{name}-db"}},
            "template": {"metadata": {"labels": db_labels}, "spec": {"containers": [{
                "name": "postgres", "image": "postgres:16-alpine",
                "env": [{"name": "POSTGRES_USER", "value": "app"}, {"name": "POSTGRES_DB", "value": "app"},
                        {"name": "POSTGRES_PASSWORD", "valueFrom": {"secretKeyRef": {"name": env_secret, "key": "POSTGRES_PASSWORD"}}},
                        {"name": "PGDATA", "value": "/var/lib/postgresql/data/pgdata"}],
                "ports": [{"containerPort": 5432}],
                "readinessProbe": {"exec": {"command": ["pg_isready", "-U", "app"]}, "periodSeconds": 5},
                "volumeMounts": [{"name": "data", "mountPath": "/var/lib/postgresql/data"}],
                "resources": {"requests": {"cpu": "100m", "memory": "256Mi"}, "limits": {"memory": "512Mi"}},
            }]}},
            "volumeClaimTemplates": [db_volume]}},
        {"apiVersion": "v1", "kind": "Service", "metadata": meta(f"{name}-db", db_labels), "spec": {
            "selector": {"app": f"{name}-db"}, "ports": [{"port": 5432}], "clusterIP": "None"}},
        # Migrations run once per version, before the new pods start (so two replicas never race).
        {"apiVersion": "batch/v1", "kind": "Job", "metadata": meta(f"{name}-migrate-{version}"), "spec": {
            "backoffLimit": 4, "ttlSecondsAfterFinished": 3600, "template": {"metadata": {"labels": {**app_labels, "role": "migrate"}}, "spec": {
                "restartPolicy": "OnFailure", "securityContext": pod_security, "initContainers": code_init,
                "volumes": code_volumes,
                "containers": [{**container, "name": "migrate", "command": ["sh", "-c", MIGRATE[spec.stack]],
                                "resources": {"requests": {"cpu": "100m", "memory": "128Mi"}, "limits": {"memory": "512Mi"}}}],
            }}}},
        {"apiVersion": "apps/v1", "kind": "Deployment", "metadata": meta(name), "spec": {
            "replicas": 2, "selector": {"matchLabels": {"app": name}},
            "strategy": {"rollingUpdate": {"maxUnavailable": 0, "maxSurge": 1}},
            "template": {"metadata": {"labels": app_labels, "annotations": {"vibestack/version": str(version)}}, "spec": {
                "securityContext": pod_security, "initContainers": code_init, "volumes": code_volumes,
                "terminationGracePeriodSeconds": 30,
                "containers": [{**container, "name": "app", "command": ["sh", "-c", f"exec {SERVE[spec.stack]}"],
                                "ports": [{"containerPort": PORT}],
                                "readinessProbe": {"httpGet": {"path": "/ready", "port": PORT}, "periodSeconds": 5},
                                "livenessProbe": {"httpGet": {"path": "/health", "port": PORT}, "initialDelaySeconds": 15, "periodSeconds": 20},
                                "resources": {"requests": {"cpu": "100m", "memory": "192Mi"}, "limits": {"memory": "512Mi"}}}],
            }}}},
        {"apiVersion": "v1", "kind": "Service", "metadata": meta(name), "spec": {
            "selector": {"app": name}, "ports": [{"port": 80, "targetPort": PORT}]}},
        {"apiVersion": "networking.k8s.io/v1", "kind": "Ingress", "metadata": {
            **meta(name), "annotations": {"nginx.ingress.kubernetes.io/proxy-buffering": "off",
                                          **({"cert-manager.io/cluster-issuer": tls_issuer} if tls_issuer else {})}},
            "spec": {"ingressClassName": ingress_class,
                     **({"tls": [{"hosts": [host], "secretName": f"{name}-tls"}]} if tls_issuer else {}),
                     "rules": [{"host": host, "http": {"paths": [{"path": "/", "pathType": "Prefix", "backend": {
                         "service": {"name": name, "port": {"number": 80}}}}]}}]}},
        # Only the app may talk to the database.
        {"apiVersion": "networking.k8s.io/v1", "kind": "NetworkPolicy", "metadata": meta(f"{name}-db-access", db_labels), "spec": {
            "podSelector": {"matchLabels": {"app": f"{name}-db"}}, "policyTypes": ["Ingress"],
            "ingress": [{"from": [{"podSelector": {"matchLabels": {"app": name}}}], "ports": [{"port": 5432}]}]}},
    ]
    return objects


def to_yaml(objects) -> str:
    return yaml.safe_dump_all(objects, sort_keys=False)


def extra_files(spec) -> dict[str, str]:
    name = spec.project_name.replace("_", "-")
    objects = k8s_manifests(spec, name=name, image=f"your-registry/{name}:latest", host=f"{name}.example.com")
    secret = (f"# Create the app's secrets first (never commit real values):\n"
              f"#   kubectl create secret generic {name}-env --from-literal=SECRET_KEY=... "
              f"--from-literal=POSTGRES_PASSWORD=... \\\n"
              f"#     --from-literal=DATABASE_URL=postgresql+psycopg://app:<password>@{name}-db:5432/app "
              f"--from-literal=ADMIN_EMAILS=you@example.com\n")
    return {"k8s/app.yaml": secret + to_yaml(objects)}
