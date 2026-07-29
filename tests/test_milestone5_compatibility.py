"""Python 3.12 syntax compatibility for Milestone 5 files."""

import ast
from pathlib import Path

import pytest

MILESTONE_5_PYTHON_FILES = [
    "app/booking_queue.py",
    "app/blueprints/requester/forms.py",
    "app/blueprints/requester/routes.py",
    "app/blueprints/scheduler/routes.py",
    "app/models/core.py",
    "tests/test_models.py",
    "tests/test_requests.py",
]


@pytest.mark.parametrize("relative_path", MILESTONE_5_PYTHON_FILES)
def test_milestone5_files_parse_as_python_312(relative_path):
    source = Path(relative_path).read_text(encoding="utf-8")
    ast.parse(source, filename=relative_path, feature_version=(3, 12))