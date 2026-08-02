"""Python 3.12 syntax compatibility for Milestone 10 files."""

import ast
from pathlib import Path

import pytest

FILES = (
    "app/__init__.py",
    "tests/test_milestone10_ui.py",
    "tests/test_milestone10_compatibility.py",
    "tests/test_milestone8_public.py",
    "app/blueprints/scheduler/routes.py",
    "tests/test_scheduling.py",
    "tests/test_reports.py",
    "tests/test_requests.py",
    "tests/test_schedule_changes.py",
    "tests/test_admin.py",
)


@pytest.mark.parametrize("filename", FILES)
def test_milestone10_python_parses_as_python_312(filename):
    source = Path(filename).read_text(encoding="utf-8")
    ast.parse(source, filename=filename, feature_version=(3, 12))
