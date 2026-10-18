"""Example plugin (extra_files): Kubernetes manifests for the generated API."""

DESCRIPTION = "Adds k8s/ manifests: a Deployment with health probes and a Service"
WHEN = "the user mentions Kubernetes, k8s, Helm, containers at scale, clusters or autoscaling"
KEYWORDS = ("kubernetes", "k8s", "helm", "cluster", "autoscal")


def extra_files(spec) -> dict[str, str]:
    name = spec.project_name.replace("_", "-")
    deployment = f"""apiVersion: apps/v1
kind: Deployment
metadata:
  name: {name}
spec:
  replicas: 2
  selector:
    matchLabels: {{app: {name}}}
  template:
    metadata:
      labels: {{app: {name}}}
    spec:
      containers:
        - name: api
          image: {name}:latest
          ports: [{{containerPort: 8000}}]
          envFrom:
            - secretRef: {{name: {name}-env}}  # DATABASE_URL, SECRET_KEY, ...
          readinessProbe: {{httpGet: {{path: /ready, port: 8000}}}}
          livenessProbe: {{httpGet: {{path: /health, port: 8000}}}}
          resources:
            requests: {{cpu: 100m, memory: 128Mi}}
            limits: {{cpu: 500m, memory: 512Mi}}
"""
    service = f"""apiVersion: v1
kind: Service
metadata:
  name: {name}
spec:
  selector: {{app: {name}}}
  ports: [{{port: 80, targetPort: 8000}}]
"""
    return {"k8s/deployment.yaml": deployment, "k8s/service.yaml": service}
