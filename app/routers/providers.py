"""
Admin API for managing SMM provider configurations.
Provides CRUD operations, per-provider service sync, and balance queries.
All endpoints require admin authentication.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import ProviderSetting, ProviderSyncLog, Service
from app.providers.base import ProviderError
from app.providers.registry import build_adapter, list_registered_slugs
from app.security import get_current_admin
from app.services.provider_sync import (
    import_services_from_all_providers,
    import_services_from_provider,
)

router = APIRouter(
    prefix="/admin/providers",
    tags=["Admin — Providers"],
    dependencies=[Depends(get_current_admin)],
)


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------

class ProviderCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    slug: str = Field(
        min_length=1,
        max_length=100,
        pattern=r"^[a-z0-9_-]+$",
        description="Unique machine identifier matching a registered adapter slug",
    )
    api_url: str = Field(min_length=1, max_length=500)
    api_key: str = Field(min_length=1, max_length=500)
    timeout_seconds: int = Field(default=30, ge=5, le=120)
    max_retries: int = Field(default=3, ge=1, le=10)
    rate_limit_rpm: int = Field(default=60, ge=0, le=3600)
    is_active: bool = True
    is_default: bool = False

    @field_validator("slug")
    @classmethod
    def slug_must_be_registered(cls, v: str) -> str:
        registered = list_registered_slugs()
        if v not in registered:
            raise ValueError(
                f"Slug '{v}' is not in the provider registry. "
                f"Registered: {registered}"
            )
        return v


class ProviderUpdateRequest(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    api_url: Optional[str] = Field(None, min_length=1, max_length=500)
    api_key: Optional[str] = Field(None, min_length=1, max_length=500)
    timeout_seconds: Optional[int] = Field(None, ge=5, le=120)
    max_retries: Optional[int] = Field(None, ge=1, le=10)
    rate_limit_rpm: Optional[int] = Field(None, ge=0, le=3600)
    is_active: Optional[bool] = None
    is_default: Optional[bool] = None


class ServiceMappingRequest(BaseModel):
    """Re-map an internal service to a different provider / provider service ID."""
    provider_slug: str
    provider_service_id: int


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _serialize_provider(p: ProviderSetting) -> dict:
    return {
        "id": p.id,
        "name": p.name,
        "slug": p.slug,
        "api_url": p.api_url,
        # Never expose the full API key
        "api_key_hint": f"...{p.api_key[-6:]}" if len(p.api_key) > 6 else "******",
        "timeout_seconds": p.timeout_seconds,
        "max_retries": p.max_retries,
        "rate_limit_rpm": p.rate_limit_rpm,
        "is_active": p.is_active,
        "is_default": p.is_default,
        "created_at": p.created_at,
        "updated_at": p.updated_at,
    }


# ---------------------------------------------------------------------------
# GET /admin/providers
# ---------------------------------------------------------------------------

@router.get("")
def list_providers(db: Session = Depends(get_db)):
    """List all configured providers."""
    providers = db.scalars(select(ProviderSetting)).all()
    return {
        "success": True,
        "count": len(providers),
        "registered_slugs": list_registered_slugs(),
        "providers": [_serialize_provider(p) for p in providers],
    }


# ---------------------------------------------------------------------------
# POST /admin/providers
# ---------------------------------------------------------------------------

@router.post("")
def create_provider(
    data: ProviderCreateRequest,
    db: Session = Depends(get_db),
):
    """Register a new SMM provider."""
    existing = db.scalar(
        select(ProviderSetting).where(ProviderSetting.slug == data.slug)
    )
    if existing:
        raise HTTPException(
            status_code=409,
            detail=f"A provider with slug '{data.slug}' already exists.",
        )

    if data.is_default:
        current_default = db.scalar(
            select(ProviderSetting).where(ProviderSetting.is_default == True)  # noqa: E712
        )
        if current_default:
            current_default.is_default = False

    provider = ProviderSetting(
        name=data.name,
        slug=data.slug,
        api_url=data.api_url,
        api_key=data.api_key,
        timeout_seconds=data.timeout_seconds,
        max_retries=data.max_retries,
        rate_limit_rpm=data.rate_limit_rpm,
        is_active=data.is_active,
        is_default=data.is_default,
    )
    db.add(provider)
    db.commit()
    db.refresh(provider)
    return {
        "success": True,
        "message": f"Provider '{data.name}' created.",
        "provider": _serialize_provider(provider),
    }


# ---------------------------------------------------------------------------
# GET /admin/providers/sync-logs  (must come BEFORE /{provider_id} to avoid conflict)
# ---------------------------------------------------------------------------

@router.get("/sync-logs")
def get_sync_logs(
    provider_slug: Optional[str] = None,
    limit: int = 50,
    db: Session = Depends(get_db),
):
    """Retrieve recent provider sync log entries."""
    stmt = select(ProviderSyncLog)
    if provider_slug:
        stmt = stmt.where(ProviderSyncLog.provider_slug == provider_slug)
    stmt = stmt.order_by(ProviderSyncLog.created_at.desc()).limit(limit)
    logs = db.scalars(stmt).all()
    return {
        "success": True,
        "count": len(logs),
        "logs": [
            {
                "id": log.id,
                "provider_slug": log.provider_slug,
                "action": log.action,
                "status": log.status,
                "records_affected": log.records_affected,
                "error_message": log.error_message,
                "created_at": log.created_at,
            }
            for log in logs
        ],
    }


# ---------------------------------------------------------------------------
# POST /admin/providers/sync-all  (must come BEFORE /{provider_id})
# ---------------------------------------------------------------------------

@router.post("/sync-all")
def sync_all_providers(db: Session = Depends(get_db)):
    """Import services from ALL active providers."""
    results = import_services_from_all_providers(db)
    return {"success": True, "results": results}


# ---------------------------------------------------------------------------
# PATCH /admin/providers/services/{service_id}/mapping  (before /{provider_id})
# ---------------------------------------------------------------------------

@router.patch("/services/{service_id}/mapping")
def remap_service(
    service_id: int,
    data: ServiceMappingRequest,
    db: Session = Depends(get_db),
):
    """
    Change which provider and provider_service_id an internal service maps to.
    Future orders will use the new mapping.
    Existing orders retain their immutable snapshot_provider_slug / snapshot_provider_service_id.
    """
    service = db.get(Service, service_id)
    if not service:
        raise HTTPException(status_code=404, detail="Service not found")

    target_provider = db.scalar(
        select(ProviderSetting).where(
            ProviderSetting.slug == data.provider_slug,
            ProviderSetting.is_active == True,  # noqa: E712
        )
    )
    if not target_provider:
        raise HTTPException(
            status_code=404,
            detail=f"No active provider with slug '{data.provider_slug}'.",
        )

    old_slug = service.provider_name
    old_pid = service.provider_service_id
    service.provider_name = data.provider_slug
    service.provider_service_id = data.provider_service_id
    db.commit()
    db.refresh(service)

    return {
        "success": True,
        "service_id": service.id,
        "previous_provider": old_slug,
        "previous_provider_service_id": old_pid,
        "new_provider": service.provider_name,
        "new_provider_service_id": service.provider_service_id,
        "message": "Mapping updated. Existing orders are unaffected (immutable snapshots).",
    }


# ---------------------------------------------------------------------------
# GET /admin/providers/{provider_id}
# ---------------------------------------------------------------------------

@router.get("/{provider_id}")
def get_provider(provider_id: int, db: Session = Depends(get_db)):
    """Get a single provider by ID."""
    p = db.get(ProviderSetting, provider_id)
    if not p:
        raise HTTPException(status_code=404, detail="Provider not found")
    return {"success": True, "provider": _serialize_provider(p)}


# ---------------------------------------------------------------------------
# PATCH /admin/providers/{provider_id}
# ---------------------------------------------------------------------------

@router.patch("/{provider_id}")
def update_provider(
    provider_id: int,
    data: ProviderUpdateRequest,
    db: Session = Depends(get_db),
):
    """Update provider configuration. Only supplied fields are changed."""
    p = db.get(ProviderSetting, provider_id)
    if not p:
        raise HTTPException(status_code=404, detail="Provider not found")

    updates = data.model_dump(exclude_unset=True)
    if updates.get("is_default"):
        current_default = db.scalar(
            select(ProviderSetting).where(
                ProviderSetting.is_default == True,  # noqa: E712
                ProviderSetting.id != provider_id,
            )
        )
        if current_default:
            current_default.is_default = False

    for field_name, value in updates.items():
        setattr(p, field_name, value)

    db.commit()
    db.refresh(p)
    return {"success": True, "provider": _serialize_provider(p)}


# ---------------------------------------------------------------------------
# DELETE /admin/providers/{provider_id}
# ---------------------------------------------------------------------------

@router.delete("/{provider_id}")
def delete_provider(
    provider_id: int,
    db: Session = Depends(get_db),
):
    """Remove a provider. Fails if any services are mapped to it."""
    p = db.get(ProviderSetting, provider_id)
    if not p:
        raise HTTPException(status_code=404, detail="Provider not found")

    service_count = db.scalar(
        select(func.count(Service.id)).where(Service.provider_name == p.slug)
    ) or 0

    if service_count > 0:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Cannot delete provider '{p.slug}': "
                f"{service_count} service(s) are still mapped to it. "
                "Re-map or delete them first."
            ),
        )

    db.delete(p)
    db.commit()
    return {"success": True, "message": f"Provider '{p.slug}' deleted."}


# ---------------------------------------------------------------------------
# GET /admin/providers/{provider_id}/balance
# ---------------------------------------------------------------------------

@router.get("/{provider_id}/balance")
def get_provider_balance(provider_id: int, db: Session = Depends(get_db)):
    """Fetch the live account balance from this provider."""
    p = db.get(ProviderSetting, provider_id)
    if not p:
        raise HTTPException(status_code=404, detail="Provider not found")
    if not p.is_active:
        raise HTTPException(status_code=400, detail="Provider is inactive")
    try:
        adapter = build_adapter(p)
        balance = adapter.get_balance()
    except ProviderError as exc:
        raise HTTPException(status_code=502, detail=f"Provider error: {exc}")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Unexpected error: {exc}")
    return {"success": True, "provider": p.slug, "balance": str(balance)}


# ---------------------------------------------------------------------------
# POST /admin/providers/{provider_id}/sync-services
# ---------------------------------------------------------------------------

@router.post("/{provider_id}/sync-services")
def sync_provider_services(
    provider_id: int,
    db: Session = Depends(get_db),
):
    """Import / update the service catalogue from this provider."""
    p = db.get(ProviderSetting, provider_id)
    if not p:
        raise HTTPException(status_code=404, detail="Provider not found")
    if not p.is_active:
        raise HTTPException(status_code=400, detail="Provider is inactive")
    try:
        result = import_services_from_provider(db, p)
    except ProviderError as exc:
        raise HTTPException(status_code=502, detail=f"Provider error: {exc}")
    return {"success": True, **result}
