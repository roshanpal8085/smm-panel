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

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ProviderSetting, ProviderSyncLog, Service
from app.pricing import calculate_selling_rate
from app.providers.base import ProviderError
from app.providers.registry import build_adapter

logger = logging.getLogger(__name__)


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

    for svc in remote_services:
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
                # Only recalculate if no manual price_override
                if existing.price_override is None:
                    existing.selling_rate = selling_rate
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
                    name=svc.name,
                    category=svc.category,
                    service_type=svc.service_type,
                    provider_rate=svc.rate,
                    selling_rate=selling_rate,
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


def import_services_from_all_providers(db: Session) -> list[dict]:
    """Import services from every active provider and return per-provider summaries."""
    active_providers = db.scalars(
        select(ProviderSetting).where(ProviderSetting.is_active == True)  # noqa: E712
    ).all()
    results: list[dict] = []
    for setting in active_providers:
        try:
            results.append(import_services_from_provider(db, setting))
        except Exception as exc:  # noqa: BLE001
            results.append({"provider": setting.slug, "error": str(exc)})
    return results
