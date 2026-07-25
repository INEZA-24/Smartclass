"""Authentication module compatibility tests."""

import ast
from pathlib import Path


def test_auth_routes_parse_as_python_312():
    source = Path("app/blueprints/auth/routes.py").read_text(encoding="utf-8")

    ast.parse(source, filename="app/blueprints/auth/routes.py", feature_version=(3, 12))
