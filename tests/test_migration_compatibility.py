"""Migration environment compatibility tests."""

import ast
from pathlib import Path


def test_migration_environment_parses_as_python_312():
    source = Path("migrations/env.py").read_text(encoding="utf-8")

    ast.parse(source, filename="migrations/env.py", feature_version=(3, 12))
