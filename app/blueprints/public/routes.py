"""Public routes."""

from flask import jsonify, render_template

from app.blueprints.public import bp


@bp.get("/")
def home() -> str:
    """Render the public landing page."""
    return render_template("public/home.html")


@bp.get("/health")
def health():
    """Return a lightweight service health response."""
    return jsonify(status="ok"), 200
