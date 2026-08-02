"""Scan Git-tracked files for focused accidental production-secret patterns."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

SECRET_PATTERNS = {
    "private key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "Render API key": re.compile(r"\brnd_[A-Za-z0-9]{20,}\b"),
    "Neon API key": re.compile(r"\bnapi_[A-Za-z0-9_-]{20,}\b"),
    "OpenAI-style API key": re.compile(r"\bsk-[A-Za-z0-9]{20,}\b"),
    "production password assignment": re.compile(
        r"(?m)^\s*(?:INITIAL_ADMIN_PASSWORD|PRODUCTION_PASSWORD)\s*=\s*\S+"
    ),
    "Neon connection URL": re.compile(
        r"postgres(?:ql)?(?:\+psycopg)?://[^\s'\"`]+\.neon\.tech[^\s'\"`]*",
        re.IGNORECASE,
    ),
}


def secret_environment_category(relative_path: str) -> str | None:
    """Classify tracked local environment filenames without reading contents."""
    name = Path(relative_path).name.lower()
    if name == ".env.example":
        return None
    if name == ".flaskenv":
        return "committed Flask environment file"
    if name == ".env" or name.startswith(".env."):
        return "committed environment file"
    return None


def controlled_match_allowed(relative: str, category: str, value: str) -> bool:
    """Allow only explicit non-secret deployment test sentinels."""
    return (
        category == "Neon connection URL"
        and relative == "tests/test_deployment.py"
        and "ep-unit-test" in value.lower()
    )


def tracked_paths(root: Path) -> list[Path]:
    """Return existing regular files known to Git."""
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=root,
        capture_output=True,
        check=True,
    )
    return [
        root / path.decode("utf-8")
        for path in result.stdout.split(b"\0")
        if path and (root / path.decode("utf-8")).is_file()
    ]


def scan_tracked_files(root: Path) -> list[tuple[str, str]]:
    """Return only affected relative paths and secret categories."""
    findings = []
    for path in tracked_paths(root):
        relative = path.relative_to(root).as_posix()
        environment_category = secret_environment_category(relative)
        if environment_category is not None:
            findings.append((relative, environment_category))
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for category, pattern in SECRET_PATTERNS.items():
            for match in pattern.finditer(content):
                if not controlled_match_allowed(relative, category, match.group(0)):
                    findings.append((relative, category))
                    break
    return findings


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    findings = scan_tracked_files(root)
    if findings:
        for path, category in findings:
            print(f"{path}: {category}", file=sys.stderr)
        return 1
    print("Tracked-file secret scan passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
