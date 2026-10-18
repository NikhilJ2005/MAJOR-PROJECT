"""Reference specs shared by the tests: simple and complex, with and without auth."""

from agent.fake_llm import BLOG_SPEC

NO_AUTH = {
    "project_name": "inventory",
    "entities": [
        {"name": "Warehouse", "fields": [{"name": "name", "unique": True}, {"name": "capacity", "type": "int", "required": False}]},
        {"name": "Item", "fields": [{"name": "sku"}, {"name": "price", "type": "float"}, {"name": "active", "type": "bool"},
                                     {"name": "restock_at", "type": "datetime", "required": False}, {"name": "warehouse_id", "references": "Warehouse"}]},
    ],
}

# Exercises every feature: admin-only and private (owner) data, enums, min values,
# decimal/date types, and custom actions with business rules.
SHOP = {
    "project_name": "shop",
    "auth": True,
    "requirements": [
        {"id": "R1", "text": "Admins manage products with a price and stock", "kind": "data"},
        {"id": "R2", "text": "Customers place private orders", "kind": "permission"},
        {"id": "R3", "text": "Checking out an order that is not pending fails with 409", "kind": "rule"},
    ],
    "entities": [
        {"name": "Product", "write_access": "admin", "requirement_ids": ["R1"], "fields": [
            {"name": "name", "unique": True}, {"name": "price", "type": "decimal", "min": 0},
            {"name": "stock", "type": "int", "min": 0}, {"name": "released_on", "type": "date", "required": False}]},
        {"name": "Order", "write_access": "owner", "requirement_ids": ["R2"], "fields": [
            {"name": "status", "choices": ["pending", "paid", "cancelled"]}, {"name": "note", "type": "text", "required": False}]},
        {"name": "OrderItem", "write_access": "owner", "fields": [
            {"name": "order_id", "references": "Order"}, {"name": "product_id", "references": "Product"},
            {"name": "quantity", "type": "int", "min": 1}]},
    ],
    "actions": [
        {"name": "checkout", "entity": "Order", "method": "POST", "path": "/{item_id}/checkout",
         "description": "Pay for an order", "rules": ["409 if the order is not pending"], "requirement_ids": ["R3"]},
        {"name": "low_stock", "entity": "Product", "method": "GET", "path": "/low-stock",
         "description": "Products whose stock is below ?threshold", "rules": []},
    ],
}

# Like the multi-agent orchestration prompt that failed on the old version: state machines,
# JSON workflow definitions, cascading deletes, indexes and live execution telemetry.
WORKFLOW = {
    "project_name": "agent_orchestrator",
    "auth": True,
    "entities": [
        {"name": "AgentNode", "write_access": "owner", "realtime": True, "fields": [
            {"name": "name", "unique": True}, {"name": "role", "choices": ["planner", "worker", "critic"]},
            {"name": "status", "choices": ["idle", "busy", "offline"], "index": True,
             "transitions": {"idle": ["busy", "offline"], "busy": ["idle"], "offline": ["idle"]}},
            {"name": "config", "type": "json", "required": False}]},
        {"name": "TaskGraph", "write_access": "owner", "fields": [
            {"name": "title"}, {"name": "definition", "type": "json"},
            {"name": "state", "choices": ["draft", "running", "done", "failed"],
             "transitions": {"draft": ["running"], "running": ["done", "failed"]}}]},
        {"name": "ExecutionLog", "write_access": "owner", "realtime": True, "fields": [
            {"name": "task_graph_id", "references": "TaskGraph", "on_delete": "cascade"},
            {"name": "agent_node_id", "references": "AgentNode", "on_delete": "set_null", "required": False},
            {"name": "level", "choices": ["info", "warning", "error"], "index": True},
            {"name": "message", "type": "text"}, {"name": "payload", "type": "json", "required": False}]},
    ],
}

# The enterprise escrow marketplace prompt: roles, a milestone state machine, wallets and a ledger,
# money-moving actions with row locks, refresh tokens, lockout, an audit log and rate limits.
ESCROW = {
    "project_name": "escrow_market",
    "auth": True,
    "features": ["refresh_tokens", "account_lockout", "audit_log", "rate_limit"],
    "roles": [{"name": "client"}, {"name": "freelancer"}, {"name": "dispute_admin", "self_signup": False},
              {"name": "super_admin"}],
    "entities": [
        {"name": "Project", "write_roles": ["client"], "fields": [
            {"name": "title"}, {"name": "budget", "type": "decimal", "min": 0},
            {"name": "status", "choices": ["open", "in_progress", "closed"],
             "transitions": {"open": ["in_progress", "closed"], "in_progress": ["closed"]}}]},
        {"name": "Bid", "write_roles": ["freelancer"], "fields": [
            {"name": "project_id", "references": "Project", "on_delete": "cascade"},
            {"name": "amount", "type": "decimal", "min": 1}, {"name": "status", "choices": ["pending", "accepted", "rejected"]}]},
        {"name": "Wallet", "write_access": "owner", "fields": [
            {"name": "balance", "type": "decimal", "min": 0}, {"name": "currency", "choices": ["USD", "EUR"]}]},
        {"name": "Milestone", "write_roles": ["client"], "fields": [
            {"name": "project_id", "references": "Project", "on_delete": "cascade"}, {"name": "title"},
            {"name": "amount", "type": "decimal", "min": 0},
            {"name": "state", "choices": ["draft", "funded", "submitted", "approved", "disputed"],
             "transitions": {"draft": ["funded"], "funded": ["submitted"], "submitted": ["approved", "disputed"],
                             "disputed": ["approved"]}}]},
        {"name": "LedgerEntry", "write_roles": ["admin"], "fields": [
            {"name": "milestone_id", "references": "Milestone", "on_delete": "restrict"},
            {"name": "amount", "type": "decimal"}, {"name": "direction", "choices": ["debit", "credit"]}]},
    ],
    "actions": [
        {"name": "fund", "entity": "Milestone", "path": "/{item_id}/fund", "roles": ["client"], "locks": True,
         "description": "Move the amount from the client's wallet into escrow", "rules": ["409 unless draft"]},
        {"name": "submit", "entity": "Milestone", "path": "/{item_id}/submit", "roles": ["freelancer"],
         "description": "Submit the work", "rules": ["409 unless funded"]},
        {"name": "approve", "entity": "Milestone", "path": "/{item_id}/approve", "roles": ["client"], "locks": True,
         "description": "Release escrow to the freelancer", "rules": ["409 unless submitted"]},
        {"name": "resolve", "entity": "Milestone", "path": "/{item_id}/resolve", "roles": ["dispute_admin"], "locks": True,
         "description": "Settle a dispute", "rules": ["409 unless disputed"]},
    ],
}

ALL_SPECS = {"blog": BLOG_SPEC, "no-auth": NO_AUTH, "shop": SHOP, "workflow": WORKFLOW, "escrow": ESCROW}
