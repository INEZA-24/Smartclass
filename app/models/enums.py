"""Database enumeration values."""

from enum import StrEnum


class StringEnum(StrEnum):
    pass


class UserRole(StringEnum):
    ADMIN = "ADMIN"
    SCHEDULER = "SCHEDULER"
    TEACHER = "TEACHER"
    MONITOR = "MONITOR"


class RequestPriority(StringEnum):
    HIGH = "HIGH"
    NORMAL = "NORMAL"


class RequestStatus(StringEnum):
    PENDING = "PENDING"
    SCHEDULED = "SCHEDULED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"


class PrepPeriod(StringEnum):
    PREP_1 = "PREP_1"
    PREP_2 = "PREP_2"


class BlockScope(StringEnum):
    SLOT = "SLOT"
    ROOM_DAY = "ROOM_DAY"
    DAY = "DAY"


class NotificationType(StringEnum):
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    RESCHEDULED = "RESCHEDULED"
    CANCELLED = "CANCELLED"
    # Notification type name, not a credential.
    PASSWORD_RESET = "PASSWORD_RESET"  # nosec B105
    SYSTEM = "SYSTEM"
