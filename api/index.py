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

    def _clean_path(path: str) -> str:
        """
        Strips Vercel serverless file prefixes so FastAPI routes match cleanly.
        e.g. '/api/index.py' -> '/'
             '/api/index.py/login' -> '/login'
             '/api/index' -> '/'
        """
        if not path:
            return "/"
        for prefix in ("/api/index.py", "/api/index", "/api"):
            if path == prefix or path == prefix + "/":
                return "/"
            if path.startswith(prefix + "/"):
                path = path[len(prefix):]
                break
        if not path.startswith("/"):
            path = "/" + path
        return path

    async def app(scope, receive, send):
        """
        ASGI middleware wrapper for Vercel.
        Normalizes scope['path'] to strip any rewrite/file prefixes.
        """
        if scope["type"] in ("http", "websocket"):
            current_path = scope.get("path", "/")
            scope["path"] = _clean_path(current_path)

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
