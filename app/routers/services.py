from datetime import datetime
from decimal import Decimal
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import CategoryMargin, PriceAuditLog, ProviderSetting, Service, SystemSetting, User
from app.services.provider_sync import (
    import_services_from_all_providers,
    import_services_from_provider,
)
from app.pricing import (
    calculate_gross_profit,
    calculate_margin_percent,
    calculate_markup_from_rates,
    calculate_selling_rate,
    get_effective_markup_percent,
    get_global_markup_percent,
)
from app.providers.vipsmmpro import VipSmmProClient
from app.security import get_current_admin

router = APIRouter(
    prefix="/services",
    tags=["Services"]
)


# ---------------------------------------------------------------------------
# Pydantic Request Models
# ---------------------------------------------------------------------------

class PriceUpdateRequest(BaseModel):
    selling_rate: Decimal = Field(gt=0, description="Customer selling rate per 1,000 units")
    notes: Optional[str] = None


class ServiceToggleRequest(BaseModel):
    is_active: Optional[bool] = None
    customer_visible: Optional[bool] = None


class BulkVisibilityRequest(BaseModel):
    service_ids: List[int]
    action: str = Field(pattern="^(show|hide|enable|disable)$")


class BulkPriceAdjustRequest(BaseModel):
    service_ids: Optional[List[int]] = None
    category: Optional[str] = None
    action: str = Field(pattern="^(increase_percent|decrease_percent|increase_fixed|decrease_fixed|apply_markup|reset_auto)$")
    value: Optional[Decimal] = Field(default=None, ge=0)


class CategoryMarkupRequest(BaseModel):
    category: str
    markup_percent: Decimal = Field(ge=0)


class GlobalMarkupRequest(BaseModel):
    markup_percent: Decimal = Field(ge=0)


# ---------------------------------------------------------------------------
# Public / Customer Services Listing
# Strictly returns customer_visible, provider_available, and is_active services
# ---------------------------------------------------------------------------

@router.get("")
def get_services(
    category: Optional[str] = None,
    search: Optional[str] = None,
    active_only: bool = True,
    db: Session = Depends(get_db)
):
    stmt = select(Service)

    if active_only:
        stmt = stmt.where(
            Service.is_active == True,
            Service.customer_visible == True,
            Service.provider_available == True
        )

    if category and category.strip():
        stmt = stmt.where(Service.category == category.strip())

    if search and search.strip():
        query = f"%{search.strip().lower()}%"
        stmt = stmt.where(
            func.lower(Service.name).like(query)
            | func.lower(Service.category).like(query)
        )

    stmt = stmt.order_by(Service.category, Service.id)
    services = db.scalars(stmt).all()

    return {
        "success": True,
        "count": len(services),
        "services": [
            {
                "id": s.id,
                "provider_service_id": s.provider_service_id,
                "name": s.name,
                "category": s.category,
                "service_type": s.service_type,
                "rate": str(s.selling_rate),
                "min": s.min_quantity,
                "max": s.max_quantity,
                "refill": s.refill_available,
                "cancel": s.cancel_available,
                "is_active": s.is_active,
                "provider_available": s.provider_available,
                "customer_visible": s.customer_visible,
            }
            for s in services
        ]
    }


@router.get("/categories")
def get_categories(
    db: Session = Depends(get_db)
):
    results = db.execute(
        select(
            Service.category,
            func.count(Service.id).label("count")
        )
        .where(
            Service.is_active == True,
            Service.customer_visible == True,
            Service.provider_available == True
        )
        .group_by(Service.category)
        .order_by(Service.category)
    ).all()

    return {
        "success": True,
        "categories": [
            {
                "name": r[0],
                "count": r[1]
            }
            for r in results
        ]
    }


@router.get("/{service_id}")
def get_service_details(
    service_id: int,
    db: Session = Depends(get_db)
):
    service = db.get(Service, service_id)
    if not service or not service.is_active or not service.customer_visible:
        raise HTTPException(status_code=404, detail="Service not found or unavailable")

    return {
        "success": True,
        "service": {
            "id": service.id,
            "provider_service_id": service.provider_service_id,
            "name": service.name,
            "category": service.category,
            "service_type": service.service_type,
            "rate": str(service.selling_rate),
            "min": service.min_quantity,
            "max": service.max_quantity,
            "refill": service.refill_available,
            "cancel": service.cancel_available,
            "is_active": service.is_active,
            "provider_available": service.provider_available,
        }
    }


# ---------------------------------------------------------------------------
# Admin Service Catalog & Pricing Management API
# Restricted exclusively to authorized administrators
# ---------------------------------------------------------------------------

@router.get("/admin/catalog")
def get_admin_catalog(
    category: Optional[str] = None,
    provider: Optional[str] = None,
    platform: Optional[str] = None,
    search: Optional[str] = None,
    status_filter: Optional[str] = Query(None, description="all, active, disabled, hidden, unavailable"),
    limit: int = 100,
    offset: int = 0,
    admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    stmt = select(Service)

    platform_keywords = {
        "youtube": ["youtube", "yt "],
        "facebook": ["facebook", "fb "],
        "instagram": ["instagram", "ig "],
        "tiktok": ["tiktok", "tik tok"],
        "telegram": ["telegram", "tg "],
        "spotify": ["spotify"],
        "threads": ["threads"],
        "twitter": ["twitter", " x ", "x.com", "x -", "tweet", "𝕩"],
        "linkedin": ["linkedin"],
        "kick": ["kick"],
    }

    if platform and platform.strip() and platform.lower() != "all":
        plat = platform.strip().lower()
        if plat in platform_keywords:
            conds = [func.lower(Service.category).like(f"%{k}%") for k in platform_keywords[plat]]
            stmt = stmt.where(or_(*conds))
        elif plat == "other":
            all_kw = [k for kws in platform_keywords.values() for k in kws]
            conds = [~func.lower(Service.category).like(f"%{k}%") for k in all_kw]
            stmt = stmt.where(and_(*conds))

    if category and category.strip() and category.lower() != "all":
        stmt = stmt.where(Service.category == category.strip())

    if provider and provider.strip() and provider.lower() != "all":
        stmt = stmt.where(Service.provider_name == provider.strip())

    if search and search.strip():
        q = f"%{search.strip().lower()}%"
        stmt = stmt.where(
            func.lower(Service.name).like(q)
            | func.lower(Service.category).like(q)
            | func.cast(Service.id, func.TEXT).like(q)
            | func.cast(Service.provider_service_id, func.TEXT).like(q)
        )

    if status_filter:
        sf = status_filter.lower()
        if sf == "active":
            stmt = stmt.where(Service.is_active == True, Service.customer_visible == True, Service.provider_available == True)
        elif sf == "disabled":
            stmt = stmt.where(Service.is_active == False)
        elif sf == "hidden":
            stmt = stmt.where(Service.customer_visible == False)
        elif sf == "unavailable":
            stmt = stmt.where(Service.provider_available == False)

    total_count = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    services = db.scalars(stmt.order_by(Service.category, Service.id).limit(limit).offset(offset)).all()

    global_markup = get_global_markup_percent(db)

    # Categories list with category markup
    cat_margins = {cm.category: cm.markup_percent for cm in db.scalars(select(CategoryMargin)).all()}

    return {
        "success": True,
        "total": total_count,
        "count": len(services),
        "global_markup_percent": str(global_markup),
        "services": [
            {
                "id": s.id,
                "provider_service_id": s.provider_service_id,
                "provider_name": s.provider_name,
                "name": s.name,
                "category": s.category,
                "service_type": s.service_type,
                "provider_rate": str(s.provider_rate),
                "selling_rate": str(s.selling_rate),
                "price_override": str(s.price_override) if s.price_override is not None else None,
                "manual_markup_percent": str(s.manual_markup_percent) if s.manual_markup_percent is not None else None,
                "effective_markup": str(calculate_markup_from_rates(s.selling_rate, s.provider_rate)),
                "gross_profit": str(calculate_gross_profit(s.selling_rate, s.provider_rate)),
                "margin_percent": str(calculate_margin_percent(s.selling_rate, s.provider_rate)),
                "is_active": s.is_active,
                "customer_visible": s.customer_visible,
                "provider_available": s.provider_available,
                "is_orderable": s.is_active and s.customer_visible and s.provider_available,
                "min": s.min_quantity,
                "max": s.max_quantity,
                "refill": s.refill_available,
                "cancel": s.cancel_available,
                "last_sync_at": s.last_sync_at,
            }
            for s in services
        ],
        "category_margins": {k: str(v) for k, v in cat_margins.items()},
        "providers": [
            {"slug": p.slug, "name": p.name}
            for p in db.scalars(select(ProviderSetting).where(ProviderSetting.is_active == True)).all()
        ]
    }


# ---------------------------------------------------------------------------
# Provider API Synchronization
# ---------------------------------------------------------------------------

@router.post("/sync")
def sync_services(
    provider: Optional[str] = Query(None, description="Provider slug or none for all"),
    admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    now = datetime.utcnow()
    if provider and provider.strip() and provider.lower() != "all":
        p_setting = db.scalar(
            select(ProviderSetting).where(
                ProviderSetting.slug == provider.strip(),
                ProviderSetting.is_active == True
            )
        )
        if not p_setting:
            raise HTTPException(status_code=404, detail=f"Active provider '{provider}' not found.")
        try:
            res = import_services_from_provider(db, p_setting)
            sync_setting = db.scalar(select(SystemSetting).where(SystemSetting.key == "last_provider_sync"))
            if sync_setting:
                sync_setting.value = now.isoformat()
            else:
                db.add(SystemSetting(key="last_provider_sync", value=now.isoformat(), description="Last provider sync timestamp"))
            db.commit()
            return {
                "success": True,
                "created": res.get("imported", 0),
                "updated": res.get("updated", 0),
                "skipped": res.get("skipped", 0),
                "errors": res.get("errors", 0),
                "details": [res],
                "synced_at": now.isoformat()
            }
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"Failed to sync provider {provider}: {str(e)}")

    # Sync all active providers
    results = import_services_from_all_providers(db)
    total_created = sum(r.get("imported", 0) for r in results if isinstance(r, dict))
    total_updated = sum(r.get("updated", 0) for r in results if isinstance(r, dict))

    sync_setting = db.scalar(select(SystemSetting).where(SystemSetting.key == "last_provider_sync"))
    if sync_setting:
        sync_setting.value = now.isoformat()
    else:
        db.add(SystemSetting(key="last_provider_sync", value=now.isoformat(), description="Last provider sync timestamp"))
    db.commit()

    return {
        "success": True,
        "created": total_created,
        "updated": total_updated,
        "details": results,
        "synced_at": now.isoformat()
    }


# ---------------------------------------------------------------------------
# Manual Price & Markup Endpoints
# ---------------------------------------------------------------------------

@router.patch("/{service_id}/price")
def update_service_price(
    service_id: int,
    data: PriceUpdateRequest,
    admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    service = db.get(Service, service_id)
    if not service:
        raise HTTPException(status_code=404, detail="Service not found")

    old_price = service.selling_rate
    service.selling_rate = data.selling_rate
    service.price_override = data.selling_rate

    audit = PriceAuditLog(
        service_id=service.id,
        admin_id=admin.id,
        old_price=old_price,
        new_price=data.selling_rate,
        change_type="manual_override",
        details=data.notes or f"Manual price set by admin {admin.username}"
    )
    db.add(audit)
    db.commit()
    db.refresh(service)

    return {
        "success": True,
        "service_id": service.id,
        "old_price": str(old_price),
        "new_price": str(service.selling_rate),
        "provider_rate": str(service.provider_rate),
        "profit": str(calculate_gross_profit(service.selling_rate, service.provider_rate)),
        "margin_percent": str(calculate_margin_percent(service.selling_rate, service.provider_rate))
    }


@router.post("/{service_id}/reset-price")
def reset_service_price_to_auto(
    service_id: int,
    admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    """Resets manual price override so service calculates dynamically from provider cost & markup."""
    service = db.get(Service, service_id)
    if not service:
        raise HTTPException(status_code=404, detail="Service not found")

    old_price = service.selling_rate
    service.price_override = None
    service.manual_markup_percent = None

    auto_price = calculate_selling_rate(
        provider_rate=service.provider_rate,
        service=service,
        db=db
    )
    service.selling_rate = auto_price

    audit = PriceAuditLog(
        service_id=service.id,
        admin_id=admin.id,
        old_price=old_price,
        new_price=auto_price,
        change_type="reset_auto",
        details=f"Reset to automatic markup by admin {admin.username}"
    )
    db.add(audit)
    db.commit()
    db.refresh(service)

    return {
        "success": True,
        "service_id": service.id,
        "new_price": str(service.selling_rate),
        "message": f"Service #{service.id} reset to automatic pricing (₹{service.selling_rate})"
    }


@router.patch("/{service_id}/toggle")
def toggle_service(
    service_id: int,
    data: ServiceToggleRequest,
    admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    service = db.get(Service, service_id)
    if not service:
        raise HTTPException(status_code=404, detail="Service not found")

    if data.is_active is not None:
        service.is_active = data.is_active
    if data.customer_visible is not None:
        service.customer_visible = data.customer_visible

    db.commit()
    db.refresh(service)

    return {
        "success": True,
        "service_id": service.id,
        "is_active": service.is_active,
        "customer_visible": service.customer_visible,
        "is_orderable": service.is_active and service.customer_visible and service.provider_available
    }


# ---------------------------------------------------------------------------
# Bulk Visibility & Pricing Operations
# ---------------------------------------------------------------------------

@router.post("/admin/bulk-visibility")
def bulk_visibility_action(
    data: BulkVisibilityRequest,
    admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    services = db.scalars(select(Service).where(Service.id.in_(data.service_ids))).all()
    if not services:
        raise HTTPException(status_code=404, detail="No matching services found")

    for s in services:
        if data.action == "show":
            s.customer_visible = True
        elif data.action == "hide":
            s.customer_visible = False
        elif data.action == "enable":
            s.is_active = True
        elif data.action == "disable":
            s.is_active = False

    db.commit()

    return {
        "success": True,
        "action": data.action,
        "affected_count": len(services),
        "message": f"Applied '{data.action}' to {len(services)} services."
    }


@router.post("/admin/bulk-price")
def bulk_price_adjustment(
    data: BulkPriceAdjustRequest,
    admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    stmt = select(Service)
    if data.service_ids:
        stmt = stmt.where(Service.id.in_(data.service_ids))
    elif data.category:
        stmt = stmt.where(Service.category == data.category.strip())
    else:
        raise HTTPException(status_code=400, detail="Provide either service_ids or category")

    services = db.scalars(stmt).all()
    if not services:
        raise HTTPException(status_code=404, detail="No matching services found")

    updated = 0
    for s in services:
        old_price = s.selling_rate
        new_price = old_price

        if data.action == "increase_percent":
            if data.value is None:
                raise HTTPException(status_code=400, detail="Value percentage required")
            new_price = (old_price * (Decimal("1") + (data.value / Decimal("100")))).quantize(Decimal("0.0001"))
            s.selling_rate = new_price
            s.price_override = new_price
        elif data.action == "decrease_percent":
            if data.value is None:
                raise HTTPException(status_code=400, detail="Value percentage required")
            new_price = (old_price * (Decimal("1") - (data.value / Decimal("100")))).quantize(Decimal("0.0001"))
            if new_price <= Decimal("0"):
                new_price = Decimal("0.0001")
            s.selling_rate = new_price
            s.price_override = new_price
        elif data.action == "increase_fixed":
            if data.value is None:
                raise HTTPException(status_code=400, detail="Fixed value required")
            new_price = (old_price + data.value).quantize(Decimal("0.0001"))
            s.selling_rate = new_price
            s.price_override = new_price
        elif data.action == "decrease_fixed":
            if data.value is None:
                raise HTTPException(status_code=400, detail="Fixed value required")
            new_price = max(Decimal("0.0001"), old_price - data.value).quantize(Decimal("0.0001"))
            s.selling_rate = new_price
            s.price_override = new_price
        elif data.action == "apply_markup":
            # Apply configured markup
            new_price = calculate_selling_rate(s.provider_rate, data.value, service=s, db=db)
            s.selling_rate = new_price
            if data.value is not None:
                s.manual_markup_percent = data.value
        elif data.action == "reset_auto":
            s.price_override = None
            s.manual_markup_percent = None
            new_price = calculate_selling_rate(s.provider_rate, service=s, db=db)
            s.selling_rate = new_price

        audit = PriceAuditLog(
            service_id=s.id,
            admin_id=admin.id,
            old_price=old_price,
            new_price=new_price,
            change_type=f"bulk_{data.action}",
            details=f"Bulk action {data.action} applied by admin {admin.username}"
        )
        db.add(audit)
        updated += 1

    db.commit()

    return {
        "success": True,
        "action": data.action,
        "updated_count": updated,
        "message": f"Successfully updated {updated} services via bulk action '{data.action}'."
    }


# ---------------------------------------------------------------------------
# Category & Global Markup Settings
# ---------------------------------------------------------------------------

@router.post("/admin/category-markup")
def set_category_markup(
    data: CategoryMarkupRequest,
    admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    cat_margin = db.scalar(select(CategoryMargin).where(CategoryMargin.category == data.category.strip()))
    if cat_margin:
        cat_margin.markup_percent = data.markup_percent
    else:
        cat_margin = CategoryMargin(
            category=data.category.strip(),
            markup_percent=data.markup_percent
        )
        db.add(cat_margin)

    # Recalculate rates for all non-overridden services in this category
    services = db.scalars(
        select(Service).where(
            Service.category == data.category.strip(),
            Service.price_override.is_(None)
        )
    ).all()

    for s in services:
        s.selling_rate = calculate_selling_rate(s.provider_rate, markup_percent=data.markup_percent)

    db.commit()

    return {
        "success": True,
        "category": data.category,
        "markup_percent": str(data.markup_percent),
        "recalculated_services": len(services),
        "message": f"Category '{data.category}' markup updated to {data.markup_percent}%. Recalculated {len(services)} automatic services."
    }


@router.post("/admin/global-markup")
def set_global_markup(
    data: GlobalMarkupRequest,
    admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    setting = db.scalar(select(SystemSetting).where(SystemSetting.key == "default_markup_percent"))
    if setting:
        setting.value = str(data.markup_percent)
    else:
        db.add(SystemSetting(key="default_markup_percent", value=str(data.markup_percent), description="Global default markup percentage"))

    # Recalculate automatic services that don't have custom override or category margin
    custom_categories = {cm.category for cm in db.scalars(select(CategoryMargin)).all()}
    auto_services = db.scalars(
        select(Service).where(
            Service.price_override.is_(None),
            Service.manual_markup_percent.is_(None)
        )
    ).all()

    recalc_count = 0
    for s in auto_services:
        if s.category not in custom_categories:
            s.selling_rate = calculate_selling_rate(s.provider_rate, markup_percent=data.markup_percent)
            recalc_count += 1

    db.commit()

    return {
        "success": True,
        "global_markup_percent": str(data.markup_percent),
        "recalculated_services": recalc_count,
        "message": f"Global markup set to {data.markup_percent}%. Updated {recalc_count} automatic services."
    }


@router.get("/admin/price-audits")
def get_price_audit_logs(
    service_id: Optional[int] = None,
    limit: int = 50,
    admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    stmt = select(PriceAuditLog)
    if service_id:
        stmt = stmt.where(PriceAuditLog.service_id == service_id)

    stmt = stmt.order_by(PriceAuditLog.created_at.desc()).limit(limit)
    logs = db.scalars(stmt).all()

    return {
        "success": True,
        "count": len(logs),
        "logs": [
            {
                "id": l.id,
                "service_id": l.service_id,
                "admin_id": l.admin_id,
                "admin_name": l.admin.username if l.admin else "Admin",
                "old_price": str(l.old_price),
                "new_price": str(l.new_price),
                "change_type": l.change_type,
                "details": l.details,
                "created_at": l.created_at.isoformat(),
            }
            for l in logs
        ]
    }