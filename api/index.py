# api/index.py
# ---------------------------------------------------------------------------
# Vercel Python runtime entry point.
#
# Vercel automatically discovers ASGI callables in files named:
#   api/index.py  →  exports a top-level `app` or `handler`
#
# We simply import the FastAPI application instance from the existing
# app package.  No code is duplicated or rewritten here.
# ---------------------------------------------------------------------------
from app.main import app  # noqa: F401 — Vercel picks up `app` from this module

# `app` is re-exported automatically by the import above.
# Vercel's Python serverless runtime wraps the ASGI callable and handles
# routing, so no additional wiring is needed.
