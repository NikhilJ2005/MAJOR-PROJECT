"""Example plugin (extra_files): a Postman collection for every generated API."""

import json

DESCRIPTION = "Adds postman_collection.json so testers can try every endpoint in Postman"


def extra_files(spec) -> dict[str, str]:
    items = []
    if spec.auth:
        body = json.dumps({"email": "me@example.com", "password": "change-me-123"})
        for name in ("register", "login"):
            items.append({"name": f"auth {name}", "request": {
                "method": "POST", "url": f"{{{{base_url}}}}/auth/{name}",
                "header": [{"key": "Content-Type", "value": "application/json"}],
                "body": {"mode": "raw", "raw": body}}})
    for e in spec.entities:
        for method, path in (("GET", "/"), ("POST", "/"), ("GET", "/1"), ("PATCH", "/1"), ("DELETE", "/1")):
            items.append({"name": f"{e.name} {method} {path}", "request": {
                "method": method, "url": f"{{{{base_url}}}}/{e.table}{path}",
                "header": [{"key": "Authorization", "value": "Bearer {{token}}"}]}})
    for a in spec.actions:
        table = spec.entity(a.entity).table
        items.append({"name": f"{a.entity} {a.name}", "request": {
            "method": a.method, "url": f"{{{{base_url}}}}/{table}{a.path.replace('{item_id}', '1')}",
            "header": [{"key": "Authorization", "value": "Bearer {{token}}"}]}})
    collection = {
        "info": {"name": spec.project_name, "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json"},
        "variable": [{"key": "base_url", "value": "http://localhost:8000"}, {"key": "token", "value": ""}],
        "item": items,
    }
    return {"postman_collection.json": json.dumps(collection, indent=1) + "\n"}
