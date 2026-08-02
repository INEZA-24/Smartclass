"""Non-mutating deployment smoke checks for the public application surface."""

from __future__ import annotations

import argparse
import json
import sys
from http.client import HTTPException
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

TIMEOUT_SECONDS = 10
MAX_RESPONSE_BYTES = 262_144
TRACEBACK_MARKERS = (b"traceback (most recent call last)", b"werkzeug debugger")
PUBLIC_MARKER = b"Smart Class Management System"
LOGIN_MARKER = b"Login securely"
NOT_FOUND_MARKER = b"Error 404"
PRIVATE_404_MARKERS = (b"Notifications", b"/admin/users", b"DATABASE_URL")
INTERNAL_DETAIL_MARKERS = (b"sqlalchemy", b"postgresql://", b"/home/", b"c:\\")


class RejectRedirects(HTTPRedirectHandler):
    """Return redirect responses to the caller without following them."""

    def redirect_request(self, *_args, **_kwargs):
        return None


def normalize_base_url(value: str) -> str:
    """Validate and normalize one credential-free HTTPS origin."""
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as error:
        raise ValueError("--base-url must be a valid HTTPS origin") from error
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        raise ValueError("--base-url must be a valid HTTPS origin")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("--base-url must not contain credentials")
    if parsed.path not in ("", "/") or parsed.query or parsed.fragment:
        raise ValueError("--base-url must contain only an HTTPS origin")
    host = parsed.hostname.lower()
    if ":" in host:
        host = f"[{host}]"
    netloc = host if port is None else f"{host}:{port}"
    return urlunsplit(("https", netloc, "", "", ""))


def fetch(base_url: str, path: str, *, follow_redirects: bool = True):
    """Fetch a bounded public response without cookies or credentials."""
    request = Request(urljoin(f"{base_url}/", path.lstrip("/")), method="GET")
    opener = build_opener() if follow_redirects else build_opener(RejectRedirects)
    try:
        with opener.open(request, timeout=TIMEOUT_SECONDS) as response:
            return (
                response.status,
                response.read(MAX_RESPONSE_BYTES),
                response.headers,
            )
    except HTTPError as error:
        return error.code, error.read(MAX_RESPONSE_BYTES), error.headers


def _require_status(label: str, status: int, expected: set[int]) -> None:
    if status not in expected:
        raise RuntimeError(f"{label} check returned unexpected HTTP status {status}")


def _reject_debug_output(label: str, body: bytes) -> None:
    lowered = body.lower()
    if any(marker in lowered for marker in TRACEBACK_MARKERS):
        raise RuntimeError(f"{label} check exposed a debug traceback")


def _check_protected_redirect(base_url: str, status: int, headers) -> None:
    _require_status("protected route", status, {302, 303})
    location = headers.get("Location") if headers is not None else None
    if not location:
        raise RuntimeError("protected route did not provide a login redirect")
    try:
        target = urlsplit(location)
    except ValueError as error:
        raise RuntimeError("protected route returned an unsafe redirect") from error
    if target.scheme or target.netloc or target.path != "/auth/login":
        raise RuntimeError("protected route returned an unsafe redirect")
    if urljoin(f"{base_url}/", location).split("?", 1)[0] != (
        f"{base_url}/auth/login"
    ):
        raise RuntimeError("protected route returned an unsafe redirect")


def run_checks(base_url: str) -> None:
    """Run bounded, GET-only checks against one deployment origin."""
    origin = normalize_base_url(base_url)

    status, body, _headers = fetch(origin, "/health")
    _require_status("health", status, {200})
    _reject_debug_output("health", body)
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise RuntimeError("health check returned invalid JSON") from error
    if payload != {"status": "ok"}:
        raise RuntimeError("health check returned an unexpected payload")

    status, body, _headers = fetch(origin, "/")
    _require_status("public schedule", status, {200})
    _reject_debug_output("public schedule", body)
    if PUBLIC_MARKER not in body:
        raise RuntimeError("public schedule marker was not found")

    status, body, _headers = fetch(origin, "/auth/login")
    _require_status("login", status, {200})
    _reject_debug_output("login", body)
    if LOGIN_MARKER not in body:
        raise RuntimeError("login page marker was not found")

    status, body, headers = fetch(origin, "/admin/", follow_redirects=False)
    _reject_debug_output("protected route", body)
    _check_protected_redirect(origin, status, headers)

    status, body, _headers = fetch(origin, "/deployment-smoke-missing")
    _require_status("not found", status, {404})
    _reject_debug_output("not found", body)
    lowered = body.lower()
    if NOT_FOUND_MARKER not in body:
        raise RuntimeError("custom 404 marker was not found")
    if any(marker.lower() in lowered for marker in PRIVATE_404_MARKERS):
        raise RuntimeError("custom 404 exposed private navigation")
    if any(marker in lowered for marker in INTERNAL_DETAIL_MARKERS):
        raise RuntimeError("custom 404 exposed internal details")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    arguments = parser.parse_args(argv)
    try:
        run_checks(arguments.base_url)
    except (ValueError, RuntimeError) as error:
        print(f"Smoke test failed: {error}", file=sys.stderr)
        return 1
    except (HTTPException, OSError, TimeoutError, URLError):
        print(
            "Smoke test failed: network request could not be completed",
            file=sys.stderr,
        )
        return 1
    except (AttributeError, TypeError):
        print("Smoke test failed: malformed HTTP response", file=sys.stderr)
        return 1
    print("All non-mutating deployment smoke checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
