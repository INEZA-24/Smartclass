"""Python 3.12 syntax compatibility for Milestone 9 files."""

import ast
from pathlib import Path

import pytest

FILES = (
    "app/__init__.py",
    "app/reports.py",
    "app/blueprints/reports/__init__.py",
    "app/blueprints/reports/routes.py",
    "tests/test_reports.py",
    "tests/test_milestone9_compatibility.py",
)


@pytest.mark.parametrize("filename", FILES)
def test_milestone9_python_parses_as_python_312(filename):
    source = Path(filename).read_text(encoding="utf-8")
    ast.parse(source, filename=filename, feature_version=(3, 12))
