"""Python 3.12 syntax compatibility for Milestone 8 files."""

import ast
from pathlib import Path

import pytest

FILES = (
    "app/__init__.py",
    "app/notifications.py",
    "app/models/core.py",
    "app/blueprints/public/routes.py",
    "app/blueprints/notifications/__init__.py",
    "app/blueprints/notifications/forms.py",
    "app/blueprints/notifications/routes.py",
    "tests/test_milestone8_public.py",
    "tests/test_milestone8_notifications.py",
    "tests/test_public.py",
    "tests/test_milestone8_compatibility.py",
)


@pytest.mark.parametrize("filename", FILES)
def test_milestone8_python_parses_as_python_312(filename):
    source = Path(filename).read_text(encoding="utf-8")
    ast.parse(source, filename=filename, feature_version=(3, 12))
