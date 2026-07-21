"""Application timezone dependency test."""

from zoneinfo import ZoneInfo


def test_africa_kigali_timezone_loads():
    timezone = ZoneInfo("Africa/Kigali")

    assert timezone.key == "Africa/Kigali"
