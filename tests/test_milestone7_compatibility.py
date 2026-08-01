"""Python 3.12 syntax compatibility for Milestone 7 files."""

import ast
from pathlib import Path

import pytest

FILES = (
    "app/scheduling.py",
    "app/blueprints/scheduler/forms.py",
    "app/blueprints/scheduler/routes.py",
    "tests/test_requests.py",
    "tests/test_schedule_changes.py",
    "tests/test_milestone7_routes.py",
    "tests/test_rejection.py",
    "tests/test_rescheduling.py",
    "tests/test_scheduled_cancellation.py",
    "tests/test_milestone7_concurrency.py",
    "tests/test_milestone7_privacy.py",
    "tests/test_milestone7_compatibility.py",
)


@pytest.mark.parametrize("filename", FILES)
def test_milestone7_python_parses_as_python_312(filename):
    source = Path(filename).read_text(encoding="utf-8")
    ast.parse(source, filename=filename, feature_version=(3, 12))
