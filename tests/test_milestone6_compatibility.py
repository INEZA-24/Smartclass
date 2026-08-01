"""Python 3.12 syntax compatibility for Milestone 6 Python files."""

import ast
from pathlib import Path

import pytest

MILESTONE_6_FILES = (
    "app/authz.py",
    "app/blueprints/scheduler/forms.py",
    "app/blueprints/scheduler/routes.py",
    "app/scheduling.py",
    "tests/test_requests.py",
    "tests/test_scheduling.py",
    "tests/test_milestone6_compatibility.py",
)


@pytest.mark.parametrize("filename", MILESTONE_6_FILES)
def test_milestone6_python_parses_as_python_312(filename):
    source = Path(filename).read_text(encoding="utf-8")
    ast.parse(source, filename=filename, feature_version=(3, 12))
