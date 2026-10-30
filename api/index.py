# api/index.py
# ---------------------------------------------------------------------------
# Vercel Python runtime entry point.
#
# Vercel automatically discovers ASGI callables in files named:
#   api/index.py  →  exports a top-level `app` or `handler`
# ---------------------------------------------------------------------------
import sys
import os
import traceback

# Ensure project root is in Python sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from app.main import app
except Exception as exc:
    # If anything unexpected fails during cold start, return diagnostic error
    error_trace = traceback.format_exc()
    print(f"CRITICAL ERROR loading app in Vercel: {error_trace}", file=sys.stderr)
    
    from fastapi import FastAPI
    from fastapi.responses import JSONResponse

    app = FastAPI(title="SMM PRIME - Startup Diagnostic")

    @app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
    async def catch_all(path: str):
        return JSONResponse(
            status_code=500,
            content={
                "error": "Failed to initialize application on Vercel",
                "details": str(exc),
                "traceback": error_trace.splitlines(),
                "hint": "Check Vercel Environment Variables: DATABASE_URL, JWT_SECRET_KEY, APP_ENV, etc."
            }
        )
