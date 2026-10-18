"""Example plugin (prompt_notes): ask the AI code writers for stricter input validation."""

DESCRIPTION = "Asks the AI agents to reject blank strings and trim whitespace on every text input"
WHEN = "inputs come from many kinds of users or money is involved (marketplaces, finance, multi-role apps)"
KEYWORDS = ("validation", "sanitize", "sanitise", "strict input")


def wanted(spec):
    if spec.roles or any(f.type == "decimal" for e in spec.entities for f in e.fields):
        return "several roles or money fields: blank or padded text is refused everywhere"
    return None


def prompt_notes(stack: str) -> str:
    return ("In the Pydantic Create/Update schemas, strip surrounding whitespace from text fields and reject "
            "empty or whitespace-only strings for required text fields (422).")
