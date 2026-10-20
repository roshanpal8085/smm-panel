"""
Background job functions for order and refill status synchronisation.
Uses the multi-provider sync engine; each provider is dispatched independently.
"""
from sqlalchemy import select

from app.database import SessionLocal
from app.models import Order, ProviderSetting
from app.providers.registry import build_adapter
from app.services.sync import sync_order_statuses


def run_order_status_sync():
    """Scheduled job: sync statuses for all active orders across all providers."""
    db = SessionLocal()
    try:
        result = sync_order_statuses(db)
        print("Order status sync:", result)
    except Exception as error:
        print("Order status sync failed:", error)
    finally:
        db.close()


def run_refill_status_sync():
    """Scheduled job: sync refill statuses, dispatching per provider."""
    db = SessionLocal()
    try:
        orders = db.scalars(
            select(Order).where(
                Order.refill_id.is_not(None),
                Order.refill_status.in_(["Pending", "In progress", "Processing"]),
            )
        ).all()

        if not orders:
            print("Refill sync: no active refills")
            return

        updated = 0

        for order in orders:
            slug = order.snapshot_provider_slug or "vipsmmpro"
            setting = db.scalar(
                select(ProviderSetting).where(
                    ProviderSetting.slug == slug,
                    ProviderSetting.is_active == True,  # noqa: E712
                )
            )
            if not setting:
                print(f"Refill sync: no active provider '{slug}' for order {order.id}")
                continue

            try:
                adapter = build_adapter(setting)
                status_str = adapter.get_refill_status(str(order.refill_id))
                order.refill_status = status_str
                updated += 1
            except Exception as error:
                print(f"Refill sync failed for order {order.id}: {error}")

        db.commit()
        print("Refill status sync:", {"checked": len(orders), "updated": updated})

    except Exception as error:
        print("Refill status sync failed:", error)
    finally:
        db.close()