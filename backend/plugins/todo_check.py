"""Example plugin (extra_checks): flag TODO/FIXME comments left in generated code."""

import re

from agent.bughunt import Finding

DESCRIPTION = "Review note for every TODO / FIXME left in the generated code"


def extra_checks(files: dict[str, str]) -> list[Finding]:
    found = []
    for path, source in files.items():
        if path.startswith("app/") and path.endswith(".py"):
            for number, line in enumerate(source.splitlines(), 1):
                if re.search(r"#\s*(TODO|FIXME|XXX)\b", line):
                    found.append(Finding(path, number, "todo-left", "low", line.strip()[:100]))
    return found
