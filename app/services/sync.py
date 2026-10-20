"""
Multi-provider order status synchronisation.

Groups active orders by their snapshot_provider_slug and dispatches
batch status queries to the correct provider adapter.
Falls back to 'vipsmmpro' for legacy orders without snapshot_provider_slug.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Order, ProviderSetting, ProviderSyncLog
from app.providers.base import ProviderError
from app.providers.registry import build_adapter

logger = logging.getLogger(__name__)

ACTIVE_STATUSES = {"Pending", "In progress", "Processing", "Partial"}

# Maximum order IDs per bulk-status request (provider protocol limit)
_BATCH_SIZE = 100


def sync_order_statuses(db: Session) -> dict:
    """
    Sync statuses for all active orders, grouped by provider.

    Returns:
        dict with keys: checked (int), updated (int).
    """
    orders: list[Order] = db.scalars(
        select(Order).where(
            Order.provider_order_id.is_not(None),
            Order.status.in_(ACTIVE_STATUSES),
        )
    ).all()

    if not orders:
        return {"checked": 0, "updated": 0}

    # Group orders by provider slug (legacy orders default to vipsmmpro)
    by_slug: dict[str, list[Order]] = defaultdict(list)
    for order in orders:
        slug = order.snapshot_provider_slug or "vipsmmpro"
        by_slug[slug].append(order)

    total_updated = 0

    for slug, slug_orders in by_slug.items():
        setting = db.scalar(
            select(ProviderSetting).where(
                ProviderSetting.slug == slug,
                ProviderSetting.is_active == True,  # noqa: E712
            )
        )
        if not setting:
            logger.warning(
                "No active ProviderSetting for slug '%s', skipping %d orders.",
                slug,
                len(slug_orders),
            )
            continue

        try:
            adapter = build_adapter(setting)
        except KeyError as exc:
            logger.error("No adapter registered for provider '%s': %s", slug, exc)
            continue

        # Process in batches of _BATCH_SIZE
        for start in range(0, len(slug_orders), _BATCH_SIZE):
            batch = slug_orders[start : start + _BATCH_SIZE]
            provider_ids = [str(o.provider_order_id) for o in batch]

            try:
                statuses = adapter.get_multiple_order_statuses(provider_ids)
            except ProviderError as exc:
                logger.error(
                    "Bulk status fetch failed for '%s': %s", slug, exc
                )
                for o in batch:
                    o.sync_attempts = (o.sync_attempts or 0) + 1
                    o.last_sync_error = str(exc)
                db.commit()
                continue

            order_map = {str(o.provider_order_id): o for o in batch}

            for ps in statuses:
                order = order_map.get(ps.provider_order_id)
                if not order:
                    continue

                order.sync_attempts = (order.sync_attempts or 0) + 1

                if ps.error:
                    order.last_sync_error = ps.error
                    continue

                order.last_sync_error = None
                order.status = ps.status
                if ps.start_count is not None:
                    order.start_count = ps.start_count
                if ps.remains is not None:
                    order.remains = ps.remains
                if ps.charge is not None:
                    order.provider_charge = ps.charge
                total_updated += 1

        db.add(ProviderSyncLog(
            provider_slug=slug,
            action="order_status_sync",
            status="success",
            records_affected=total_updated,
        ))

    db.commit()
    return {"checked": len(orders), "updated": total_updated}