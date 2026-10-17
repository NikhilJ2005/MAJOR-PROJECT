"""Example plugin (extra_checks): find secrets in every file, not only Python code.

The built-in static checks look at Python. This scan also reads YAML, JSON, TOML, .env files,
Markdown and Dockerfiles, where real keys tend to slip in. A hit fails validation (high).
"""

import re

from agent.bughunt import Finding

DESCRIPTION = "Fails validation when a real-looking key or token appears in any file (YAML, JSON, .env ...)"

PATTERNS = {
    "aws-access-key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "github-token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b"),
    "google-api-key": re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b"),
    "openai-style-key": re.compile(r"\bsk-[A-Za-z0-9_\-]{32,}\b"),
    "slack-token": re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b"),
    "private-key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
}


def extra_checks(files: dict[str, str]) -> list[Finding]:
    found = []
    for path, text in files.items():
        if path.startswith("app/static/vendor/"):
            continue  # third-party minified code
        for number, line in enumerate(text.splitlines(), 1):
            for rule, pattern in PATTERNS.items():
                if pattern.search(line):
                    found.append(Finding(path, number, rule, "high", "remove this secret; read it from an environment variable"))
    return found
