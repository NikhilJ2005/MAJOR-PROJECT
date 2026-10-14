"""Example plugin (prompt_notes): ask the AI code writers for stricter input validation."""

DESCRIPTION = "Asks the AI agents to reject blank strings and trim whitespace on every text input"


def prompt_notes(stack: str) -> str:
    return ("In the Pydantic Create/Update schemas, strip surrounding whitespace from text fields and reject "
            "empty or whitespace-only strings for required text fields (422).")
