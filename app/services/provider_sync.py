"""
Multi-provider service import and synchronisation.

Handles importing services from any registered provider, deduplicating
records by (provider_name slug, provider_service_id), and computing
selling prices.
"""
from __future__ import annotations

import logging
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import ProviderSetting, ProviderSyncLog, Service
from app.pricing import calculate_selling_rate
from app.providers.base import ProviderError
from app.providers.registry import build_adapter

logger = logging.getLogger(__name__)


def deduplicate_services(db: Session, provider_slug: str | None = None) -> int:
    """
    Remove duplicate services sharing the same (provider_name, provider_service_id).

    For each duplicate group, the row with the **lowest id** is kept.  The
    others are marked ``is_active=False, customer_visible=False`` so they
    disappear from the storefront without losing order history FK integrity.

    Args:
        db: SQLAlchemy session.
        provider_slug: If given, only deduplicate services for this provider.
            Pass ``None`` to deduplicate across all providers.

    Returns:
        Number of duplicate rows deactivated.
    """
    # Step 1 – find (provider_name, provider_service_id) combos with > 1 row
    dup_subq = (
        select(
            Service.provider_name,
            Service.provider_service_id,
            func.min(Service.id).label("keep_id"),
            func.count(Service.id).label("cnt"),
        )
        .group_by(Service.provider_name, Service.provider_service_id)
        .having(func.count(Service.id) > 1)
    )
    if provider_slug:
        dup_subq = dup_subq.where(Service.provider_name == provider_slug)

    dup_groups = db.execute(dup_subq).all()

    if not dup_groups:
        return 0

    deactivated = 0
    for row in dup_groups:
        pname, psid, keep_id, cnt = row
        # Fetch all rows in this duplicate group except the keeper
        dupes = db.scalars(
            select(Service).where(
                Service.provider_name == pname,
                Service.provider_service_id == psid,
                Service.id != keep_id,
            )
        ).all()
        for dup in dupes:
            dup.is_active = False
            dup.customer_visible = False
            deactivated += 1

    db.commit()
    if deactivated:
        logger.info(
            "Deduplicated %d duplicate service rows%s",
            deactivated,
            f" for provider={provider_slug}" if provider_slug else "",
        )
    return deactivated


def import_services_from_provider(
    db: Session,
    provider_setting: ProviderSetting,
    markup_percent: Decimal | None = None,
) -> dict:
    """
    Fetch the service catalogue from one provider and upsert into the
    local services table.

    Deduplication key: (provider_name, provider_service_id) — uses
    the existing provider_name column (which stores the slug) and the
    existing provider_service_id column.

    Args:
        db: SQLAlchemy session.
        provider_setting: Active ProviderSetting ORM record.
        markup_percent: Optional override markup; falls back to
            service/category/global defaults via calculate_selling_rate.

    Returns:
        dict with keys: provider, imported, updated, skipped, errors.
    """
    adapter = build_adapter(provider_setting)
    slug = provider_setting.slug

    # Guard: clean up any pre-existing duplicates for this provider
    deduplicate_services(db, provider_slug=slug)

    try:
        remote_services = adapter.get_services()
    except ProviderError as exc:
        db.add(ProviderSyncLog(
            provider_slug=slug,
            action="import_services",
            status="error",
            records_affected=0,
            error_message=str(exc),
        ))
        db.commit()
        raise

    imported = 0
    updated = 0
    skipped = 0
    errors = 0
    now = datetime.utcnow()
    seen_ids: set[int] = set()

    provider_offset = 0 if slug == "vipsmmpro" or getattr(provider_setting, "is_default", False) else (getattr(provider_setting, "id", 1) * 10000)

    for idx, svc in enumerate(remote_services):
        item_sort_order = provider_offset + idx
        try:
            pid = int(svc.provider_service_id)
        except (ValueError, TypeError):
            skipped += 1
            continue

        seen_ids.add(pid)

        try:
            existing = db.scalar(
                select(Service).where(
                    Service.provider_name == slug,
                    Service.provider_service_id == pid,
                )
            )

            selling_rate = calculate_selling_rate(
                provider_rate=svc.rate,
                markup_percent=markup_percent,
                service=existing,
                db=db,
            )

            if existing:
                # Update mutable catalogue fields; preserve admin overrides
                existing.name = svc.name
                existing.category = svc.category
                existing.service_type = svc.service_type
                existing.provider_rate = svc.rate
                existing.sort_order = item_sort_order
                # Only recalculate if no manual price_override
                if existing.price_override is None:
                    existing.selling_rate = selling_rate
                if getattr(svc, "description", None):
                    existing.description = svc.description
                existing.min_quantity = svc.min_quantity
                existing.max_quantity = svc.max_quantity
                existing.refill_available = svc.refill
                existing.cancel_available = svc.cancel
                existing.provider_available = True
                existing.last_sync_at = now
                updated += 1
            else:
                new_svc = Service(
                    provider_service_id=pid,
                    sort_order=item_sort_order,
                    name=svc.name,
                    category=svc.category,
                    service_type=svc.service_type,
                    provider_rate=svc.rate,
                    selling_rate=selling_rate,
                    description=getattr(svc, "description", None),
                    min_quantity=svc.min_quantity,
                    max_quantity=svc.max_quantity,
                    refill_available=svc.refill,
                    cancel_available=svc.cancel,
                    provider_name=slug,
                    provider_available=True,
                    customer_visible=True,
                    is_active=True,
                    last_sync_at=now,
                )
                db.add(new_svc)
                imported += 1

        except Exception as exc:  # noqa: BLE001
            logger.error(
                "Error syncing service %s from %s: %s", pid, slug, exc
            )
            errors += 1

    # Mark services no longer present at the provider as unavailable
    stale = db.scalars(
        select(Service).where(
            Service.provider_name == slug,
            Service.provider_available == True,  # noqa: E712
        )
    ).all()
    for svc in stale:
        if svc.provider_service_id not in seen_ids:
            svc.provider_available = False

    db.add(ProviderSyncLog(
        provider_slug=slug,
        action="import_services",
        status="success" if errors == 0 else "partial",
        records_affected=imported + updated,
        error_message=f"{errors} row error(s)" if errors else None,
    ))
    db.commit()

    return {
        "provider": slug,
        "imported": imported,
        "updated": updated,
        "skipped": skipped,
        "errors": errors,
    }


def apply_interleaved_sort_order(db: Session) -> int:
    """
    Recalculates sort_order across all active providers in an alternating
    round-robin fashion (1 service from P1, 1 from P2, 1 from P1, 1 from P2...).
    Whichever provider has more services remaining continues in its original order.
    """
    providers = db.scalars(
        select(ProviderSetting)
        .where(ProviderSetting.is_active == True)
        .order_by(
            (ProviderSetting.slug != "vipsmmpro"),
            ProviderSetting.id.asc()
        )
    ).all()

    real_providers = [p for p in providers if p.slug in ("vipsmmpro", "smmxpert")]
    if not real_providers:
        real_providers = [p for p in providers if p.api_key and p.api_url]

    if not real_providers:
        return 0

    provider_services: list[list[Service]] = []
    for p in real_providers:
        try:
            adapter = build_adapter(p)
            remote = adapter.get_services()
            id_to_idx = {int(s.provider_service_id): i for i, s in enumerate(remote)}
            db_svcs = db.scalars(
                select(Service).where(Service.provider_name == p.slug)
            ).all()
            db_svcs.sort(key=lambda s: id_to_idx.get(s.provider_service_id, 999999))
            provider_services.append(db_svcs)
        except Exception as exc:
            logger.warning("Could not fetch remote order for %s: %s", p.slug, exc)
            db_svcs = db.scalars(
                select(Service)
                .where(Service.provider_name == p.slug)
                .order_by(Service.sort_order.asc(), Service.id.asc())
            ).all()
            provider_services.append(db_svcs)

    current_order = 0
    max_len = max(len(s_list) for s_list in provider_services) if provider_services else 0

    for i in range(max_len):
        for s_list in provider_services:
            if i < len(s_list):
                svc = s_list[i]
                svc.sort_order = current_order
                current_order += 1

    db.commit()
    return current_order


def import_services_from_all_providers(db: Session) -> list[dict]:
    """Import services from every active provider, interleave their order, and return per-provider summaries."""
    active_providers = db.scalars(
        select(ProviderSetting).where(ProviderSetting.is_active == True)  # noqa: E712
    ).all()
    results: list[dict] = []
    for setting in active_providers:
        try:
            results.append(import_services_from_provider(db, setting))
        except Exception as exc:  # noqa: BLE001
            results.append({"provider": setting.slug, "error": str(exc)})

    # Interleave orders across providers: 1 from P1, 1 from P2, 1 from P1...
    try:
        apply_interleaved_sort_order(db)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not apply interleaved sort order: %s", exc)

    return results


def fetch_and_cache_service_description(db: Session, service: Service) -> str | None:
    """
    Returns the cached service description or fetches it on-demand from the upstream provider.
    """
    if service.description and service.description.strip():
        return service.description.strip()

    slug = service.provider_name or "vipsmmpro"
    provider_setting = db.scalar(
        select(ProviderSetting).where(
            ProviderSetting.slug == slug,
            ProviderSetting.is_active == True,
        )
    )
    if not provider_setting:
        # Fallback to default or any active provider setting
        provider_setting = db.scalar(
            select(ProviderSetting).where(ProviderSetting.is_active == True)
        )
    if not provider_setting:
        return None

    try:
        adapter = build_adapter(provider_setting)
        desc = adapter.get_service_description(service.provider_service_id)
        if desc and isinstance(desc, str) and desc.strip():
            service.description = desc.strip()
            db.commit()
            return service.description
    except Exception as exc:
        logger.warning(
            "Failed fetching on-demand description for svc %s from %s: %s",
            service.id,
            slug,
            exc,
        )
    return None


def sync_service_descriptions_batch(
    db: Session,
    limit: int = 100,
    provider_slug: str | None = None,
) -> dict:
    """
    Sync descriptions for active services that do not yet have one stored in DB.
    """
    stmt = (
        select(Service)
        .where(
            Service.is_active == True,
            Service.customer_visible == True,
            (Service.description == None) | (Service.description == ""),
        )
        .order_by(Service.sort_order.asc(), Service.id.asc())
    )
    if provider_slug:
        stmt = stmt.where(Service.provider_name == provider_slug)
    if limit > 0:
        stmt = stmt.limit(limit)

    services_to_sync = db.scalars(stmt).all()
    if not services_to_sync:
        return {"updated": 0, "total": 0}

    provider_cache: dict[str, Any] = {}
    updated = 0

    for svc in services_to_sync:
        slug = svc.provider_name or "vipsmmpro"
        if slug not in provider_cache:
            pset = db.scalar(
                select(ProviderSetting).where(
                    ProviderSetting.slug == slug,
                    ProviderSetting.is_active == True,
                )
            )
            provider_cache[slug] = build_adapter(pset) if pset else None

        adapter = provider_cache.get(slug)
        if not adapter:
            continue

        try:
            desc = adapter.get_service_description(svc.provider_service_id)
            if desc and isinstance(desc, str) and desc.strip():
                svc.description = desc.strip()
                updated += 1
        except Exception as exc:
            logger.debug("Error fetching desc for svc %s: %s", svc.id, exc)

    if updated > 0:
        db.commit()

    return {"updated": updated, "total": len(services_to_sync)}

