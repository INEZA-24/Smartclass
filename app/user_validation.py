"""Pure account-input normalization and validation shared by UI and CLI."""

import unicodedata

USERNAME_MAX_LENGTH = 80
FULL_NAME_MAX_LENGTH = 150
MINIMUM_PASSWORD_LENGTH = 12


def _reject_control_characters(value: str, label: str) -> None:
    if any(unicodedata.category(character).startswith("C") for character in value):
        raise ValueError(f"{label} contains unsupported characters.")


def normalize_username(value: str) -> str:
    """Apply the canonical account username normalization."""
    return value.strip().lower()


def normalize_full_name(value: str) -> str:
    """Apply the canonical account full-name normalization."""
    return " ".join(value.strip().split())


def validate_username(value: str) -> str:
    """Validate and return one normalized username."""
    _reject_control_characters(value, "Username")
    normalized = normalize_username(value)
    if not normalized or len(normalized) > USERNAME_MAX_LENGTH:
        raise ValueError("Username is invalid.")
    return normalized


def validate_full_name(value: str) -> str:
    """Validate and return one normalized full name."""
    _reject_control_characters(value, "Full name")
    normalized = normalize_full_name(value)
    if not normalized or len(normalized) > FULL_NAME_MAX_LENGTH:
        raise ValueError("Full name is invalid.")
    return normalized


def validate_temporary_password(value: str) -> str:
    """Apply the established temporary-password policy."""
    _reject_control_characters(value, "Temporary password")
    if len(value) < MINIMUM_PASSWORD_LENGTH:
        raise ValueError("Temporary password is too short.")
    return value
