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
    from app.main import app as fastapi_app

    async def app(scope, receive, send):
        """
        ASGI middleware wrapper for Vercel.
        Normalizes the request path because Vercel rewrites often pass
        '/api/index.py' in scope['path'] or provide the original route in 'x-matched-path'.
        """
        if scope["type"] in ("http", "websocket"):
            headers = dict(scope.get("headers", []))
            matched_path = headers.get(b"x-matched-path", b"").decode("utf-8")
            
            if matched_path:
                scope["path"] = matched_path
            elif scope.get("path", "").startswith("/api/index.py"):
                scope["path"] = scope["path"][len("/api/index.py"):] or "/"
            elif scope.get("path", "").startswith("/api/index"):
                scope["path"] = scope["path"][len("/api/index"):] or "/"

        await fastapi_app(scope, receive, send)

except Exception as exc:
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
