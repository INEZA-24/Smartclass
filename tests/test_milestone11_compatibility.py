"""Python 3.12 syntax compatibility checks for Milestone 11 files."""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYTHON_FILES = (
    "app/__init__.py",
    "app/blueprints/admin/forms.py",
    "app/blueprints/admin/routes.py",
    "app/blueprints/auth/forms.py",
    "app/blueprints/auth/routes.py",
    "app/blueprints/public/routes.py",
    "app/models/enums.py",
    "app/provisioning.py",
    "app/seed.py",
    "app/user_validation.py",
    "config.py",
    "scripts/secret_scan.py",
    "scripts/smoke_test.py",
    "tests/test_deployment.py",
    "tests/test_milestone11_compatibility.py",
    "wsgi.py",
)


def test_milestone11_python_files_parse_as_python_3_12():
    assert len(PYTHON_FILES) == len(set(PYTHON_FILES))
    for relative_path in PYTHON_FILES:
        path = ROOT / relative_path
        ast.parse(
            path.read_text(encoding="utf-8"),
            filename=str(path),
            feature_version=(3, 12),
        )
