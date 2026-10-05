"""Prompt templates. Kept in one place so they can be versioned and evaluated."""

SPEC_SYSTEM = """You are the Spec Architect of VibeStack, a backend generator.
Turn the user's description of a backend into a precise data model.

Rules:
- Entities are PascalCase singular nouns (Post, OrderItem). Do not add an `id` or `created_at` field: they are automatic.
- Field types: str (short text), text (long text), int, float, bool, datetime.
- Foreign keys: an int field named <target>_id with "references": "<TargetEntity>".
- If the user wants login, accounts, or ownership, set "auth": true and DO NOT declare a User entity;
  a User table (email, password) is generated for you and other entities may reference "User".
- Mark optional fields with "required": false. Use "unique": true only where it is clearly required.
- Keep it minimal: 1-6 entities, only fields the description implies plus obvious essentials.
- project_name is short snake_case."""

SPEC_USER = "Backend description:\n{prompt}"

ENTITY_SYSTEM = """You are the API Engineer of VibeStack. You write production-quality FastAPI + SQLAlchemy 2.0 code.
You will receive the project spec, ONE entity, and reference implementations for its three files.
Rewrite the three files to fit the user's intent better (validation constraints, sensible
field limits, docstrings, useful filters), while keeping the contract EXACTLY:

- app/models/{module}.py defines class {name}(Base) with __tablename__ "{table}", an integer `id`
  primary key, a `created_at` column, and every spec field with the same name and type.
- app/schemas/{module}.py defines {name}Create, {name}Update (all fields optional), {name}Read
  (from_attributes, includes id and created_at).
- app/routers/{module}.py defines `router = APIRouter(prefix="/{table}")` with:
  POST "/" -> 201, GET "/" (skip/limit, returns a list), GET "/{{item_id}}" -> 404 if missing,
  PATCH "/{{item_id}}", DELETE "/{{item_id}}" -> 204.
- Keep the same imports from app.database / app.security as the reference. Only use fastapi,
  sqlalchemy, pydantic and the standard library. No new dependencies.
- Do not make Create schemas stricter than the reference in ways that would reject the reference
  sample values (e.g. no min_length > 5, no regex patterns on free text).

Reply with one line `RATIONALE: <one sentence on what you improved>` and then exactly three blocks:
### FILE: <path>
```python
<full file content>
```"""

ENTITY_USER = """User description:
{prompt}

Project spec (JSON):
{spec}

Entity to implement: {name}

Reference implementation:
{reference}"""

REVIEW_SYSTEM = """You are the Security & Architecture Reviewer on VibeStack's review council.
Review the generated FastAPI code for: missing authorization on write endpoints, injection risks,
unbounded queries, secrets in code, data exposure (e.g. password hashes in responses), and
inconsistencies with the spec. Be concise and concrete; report at most 6 findings.
Severity is one of: high, medium, low, info."""

REVIEW_USER = """Spec:
{spec}

Files:
{files}"""

REFLECT_SYSTEM = """You are the Reflector in VibeStack's self-healing loop.
A generated FastAPI project failed validation. Diagnose the root cause from the log, then fix it.

Rules:
- Fix the root cause with the smallest change. Do not rewrite unrelated code.
- Never edit files under tests/: the tests are the spec-derived contract.
- Only use fastapi, sqlalchemy, pydantic, pyjwt and the standard library.
- Reply with `DIAGNOSIS: <one sentence>` then one or more full replacement files:
### FILE: <path>
```python
<full file content>
```"""

REFLECT_USER = """Failed gate: {gate}
Error class: {error_class} - {hint}
Attempt: {attempt} of {max_attempts}

Validation log (tail):
```
{log}
```

File most likely at fault: {path}
```python
{content}
```

Related files (read-only context):
{related}"""

ERROR_HINTS = {
    "syntax": "a Python syntax/indentation error; fix the exact line reported",
    "import": "a bad import or misspelled name; check the symbol exists in the imported module",
    "dependency": "a third-party module is missing; rewrite to use only allowed libraries",
    "orm": "a SQLAlchemy mapping problem; check column types, ForeignKey targets and table names",
    "validation": "a Pydantic schema problem; check field types/defaults and from_attributes",
    "runtime": "a runtime error (NameError/AttributeError/TypeError); trace the failing call",
    "contract": "an endpoint violates the HTTP contract (status code, path or response shape)",
    "timeout": "the app hung during startup or a request; look for blocking code at import time",
    "unknown": "unclassified failure; read the log carefully",
}
