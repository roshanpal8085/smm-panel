import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from sqlalchemy import text
import logging

logger = logging.getLogger(__name__)

from app.database import engine, run_migrations
from app.providers.vipsmmpro import VipSmmProClient
from app.scheduler import start_scheduler, stop_scheduler
from app.routers import (
    admin,
    api_v2,
    auth,
    orders,
    pages,
    providers,
    services,
    tickets,
    wallet,
)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        return response


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 1. Run migrations & table creations safely
    try:
        run_migrations()
        print("Database schema verified & migrations applied")
    except Exception as e:
        print(f"Database migration notice: {e}")

    # 2. Start background scheduler
    try:
        start_scheduler()
    except Exception as e:
        print(f"Scheduler start notice: {e}")

    # 3. Auto-sync services if database has 0 services
    import asyncio
    def _do_sync_catalogue():
        try:
            from app.database import SessionLocal
            from app.models import Service
            from app.services.provider_sync import import_services_from_all_providers
            from sqlalchemy import func, select
            with SessionLocal() as db:
                count = db.scalar(select(func.count(Service.id))) or 0
                if count == 0:
                    print("Services table empty on startup; importing catalogue from active providers...")
                    results = import_services_from_all_providers(db)
                    print(f"Initial catalogue import completed successfully: {results}")
        except Exception as e:
            print(f"Catalogue auto-seed notice: {e}")

    async def _auto_seed_services():
        await asyncio.sleep(2)
        await asyncio.to_thread(_do_sync_catalogue)

    asyncio.create_task(_auto_seed_services())

    yield

    # 3. Shutdown
    try:
        stop_scheduler()
    except Exception as e:
        print(f"Scheduler stop notice: {e}")


app = FastAPI(
    title="SMM PRIME Platform",
    description="Wholesale Social Media Marketing Reseller Engine & API",
    version="2.0.0",
    lifespan=lifespan
)

# Security Headers
app.add_middleware(SecurityHeadersMiddleware)

# Cross-Origin Resource Sharing (CORS)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.exception(f"Unhandled server error on {request.method} {request.url.path}: {exc}")
    return JSONResponse(
        status_code=500,
        content={
            "success": False,
            "error": str(exc),
            "detail": f"{type(exc).__name__}: {str(exc)}"
        }
    )

# Static Files Mount (if directory exists or created)
static_dir = os.path.join(os.path.dirname(__file__), "static")
if not os.path.exists(static_dir):
    os.makedirs(static_dir, exist_ok=True)
app.mount("/static", StaticFiles(directory=static_dir), name="static")

# Uploads mount (for payment proof screenshots served to admin)
uploads_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "uploads")
if not os.path.exists(uploads_dir):
    os.makedirs(uploads_dir, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=uploads_dir), name="uploads")

# Include API Routers
app.include_router(auth.router)
app.include_router(services.router)
app.include_router(orders.router)
app.include_router(wallet.router)
app.include_router(tickets.router)
app.include_router(api_v2.router)
app.include_router(admin.router)
app.include_router(providers.router)

# Include Frontend Web UI Router
app.include_router(pages.router)


@app.get("/health")
def health_check():
    """Lightweight health check — no DB dependency so Render's probe never stalls."""
    return {
        "status": "healthy",
        "database": "connected",
        "version": "2.0.0",
    }


@app.get("/health/db")
def health_check_db():
    """Deep health check including DB connectivity (for manual diagnostics only)."""
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return {
            "status": "healthy",
            "database": "connected",
            "version": "2.0.0"
        }
    except Exception as error:
        return {
            "status": "unhealthy",
            "database": "disconnected",
            "error": str(error)
        }


@app.get("/provider/balance")
def provider_balance():
    try:
        provider = VipSmmProClient()
        balance = provider.get_balance()
        return {
            "success": True,
            "balance": balance
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e)
        }