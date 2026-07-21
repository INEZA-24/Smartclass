"""Gunicorn-compatible WSGI entry point."""

from app import create_app

application = create_app()
