from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Order, ProviderSetting, Service, Transaction, User
from app.providers.base import ProviderError
from app.providers.registry import build_adapter
from app.routers.auth import get_current_user
from app.security import get_current_admin
from app.services.sync import sync_order_statuses
from app.services.wallet import (
    credit_wallet,
    debit_wallet,
    InsufficientBalanceError,
)

router = APIRouter(
    prefix="/orders",
    tags=["Orders"]
)


class CreateOrderRequest(BaseModel):
    service_id: int
    link: str = Field(min_length=1)
    quantity: int = Field(gt=0)


class MassOrderRequest(BaseModel):
    orders_data: str = Field(
        ...,
        description="Multi-line string in format: service_id|quantity|link"
    )


def execute_single_order(
    service_id: int,
    link: str,
    quantity: int,
    current_user: User,
    db: Session
) -> dict:
    service = db.scalar(
        select(Service).where(
            Service.id == service_id
        )
    )

    if not service:
        raise HTTPException(
            status_code=404,
            detail=f"Service #{service_id} not found"
        )

    # Strict 4-tier orderability check
    if not service.is_active:
        raise HTTPException(
            status_code=400,
            detail=f"Service #{service_id} is currently disabled by administrator."
        )

    if not service.customer_visible:
        raise HTTPException(
            status_code=400,
            detail=f"Service #{service_id} is hidden from customer ordering."
        )

    if not service.provider_available:
        raise HTTPException(
            status_code=503,
            detail=f"Service #{service_id} is temporarily unavailable at the upstream provider."
        )

    if quantity < service.min_quantity:
        raise HTTPException(
            status_code=400,
            detail=f"Minimum quantity for '{service.name}' is {service.min_quantity}"
        )

    if quantity > service.max_quantity:
        raise HTTPException(
            status_code=400,
            detail=f"Maximum quantity for '{service.name}' is {service.max_quantity}"
        )

    charge = (
        Decimal(quantity)
        / Decimal("1000")
        * service.selling_rate
    ).quantize(Decimal("0.01"))

    # 1. Debit wallet
    try:
        _, wallet_tx = debit_wallet(
            db=db,
            user_id=current_user.id,
            amount=charge,
            description=f"Order: {service.name[:30]}"
        )
        db.commit()
    except InsufficientBalanceError:
        db.rollback()
        raise HTTPException(
            status_code=400,
            detail=f"Insufficient balance. Order cost is ₹{charge}, your balance is ₹{current_user.balance}"
        )
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail=f"Unable to process wallet payment: {str(e)}"
        )

    # 2. Resolve provider configuration
    provider_setting = db.scalar(
        select(ProviderSetting).where(
            ProviderSetting.slug == service.provider_name,
            ProviderSetting.is_active == True,  # noqa: E712
        )
    )
    if not provider_setting:
        try:
            credit_wallet(
                db=db,
                user_id=current_user.id,
                amount=charge,
                transaction_type="refund",
                description="Auto-refund: Provider not configured"
            )
            db.commit()
        except Exception:
            db.rollback()
        raise HTTPException(
            status_code=503,
            detail=(
                f"Provider '{service.provider_name}' is not active or not configured. "
                "Contact administrator."
            )
        )

    try:
        adapter = build_adapter(provider_setting)
    except KeyError as ke:
        try:
            credit_wallet(
                db=db,
                user_id=current_user.id,
                amount=charge,
                transaction_type="refund",
                description="Auto-refund: Provider adapter missing"
            )
            db.commit()
        except Exception:
            db.rollback()
        raise HTTPException(status_code=503, detail=str(ke))

    # 3. Provider API request
    try:
        order_result = adapter.add_order(
            provider_service_id=str(service.provider_service_id),
            link=link.strip(),
            quantity=quantity,
        )
    except ProviderError as error:
        try:
            credit_wallet(
                db=db,
                user_id=current_user.id,
                amount=charge,
                transaction_type="refund",
                description="Auto-refund: Provider rejected order"
            )
            db.commit()
        except Exception:
            db.rollback()
        raise HTTPException(
            status_code=400,
            detail=str(error)
        )
    except Exception as error:
        try:
            credit_wallet(
                db=db,
                user_id=current_user.id,
                amount=charge,
                transaction_type="refund",
                description="Auto-refund: Provider connection failed"
            )
            db.commit()
        except Exception:
            db.rollback()
        raise HTTPException(
            status_code=502,
            detail=f"Provider request failed: {error}"
        )

    provider_order_id_str = order_result.provider_order_id

    # 4. Create Order with snapshot pricing and immutable provider snapshot
    estimated_provider_cost = (
        Decimal(quantity) / Decimal("1000") * service.provider_rate
    ).quantize(Decimal("0.0001"))
    estimated_profit = (charge - estimated_provider_cost).quantize(Decimal("0.0001"))

    new_order = Order(
        user_id=current_user.id,
        service_id=service.id,
        provider_order_id=int(provider_order_id_str) if provider_order_id_str.isdigit() else None,
        link=link.strip(),
        quantity=quantity,
        charge=charge,
        provider_charge=estimated_provider_cost,
        snapshot_rate=service.selling_rate,
        snapshot_provider_rate=service.provider_rate,
        snapshot_provider_slug=service.provider_name,
        snapshot_provider_service_id=str(service.provider_service_id),
        gross_profit=estimated_profit,
        start_count=None,
        remains=quantity,
        status="Pending"
    )

    db.add(new_order)
    db.commit()
    db.refresh(new_order)

    return {
        "success": True,
        "order_id": new_order.id,
        "provider_order_id": new_order.provider_order_id,
        "service_name": service.name,
        "charge": str(new_order.charge),
        "status": new_order.status
    }


@router.post("")
def create_order(
    data: CreateOrderRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    return execute_single_order(
        service_id=data.service_id,
        link=data.link,
        quantity=data.quantity,
        current_user=current_user,
        db=db
    )


@router.post("/mass")
def create_mass_orders(
    data: MassOrderRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    lines = [line.strip() for line in data.orders_data.strip().splitlines() if line.strip()]
    if not lines:
        raise HTTPException(status_code=400, detail="No orders provided")

    results = []
    success_count = 0
    fail_count = 0

    for idx, line in enumerate(lines, start=1):
        parts = [p.strip() for p in line.split("|")]
        if len(parts) < 3:
            results.append({
                "line": idx,
                "input": line,
                "status": "error",
                "error": "Invalid format. Expected: service_id|quantity|link"
            })
            fail_count += 1
            continue

        try:
            service_id = int(parts[0])
            quantity = int(parts[1])
            link = parts[2]
        except ValueError:
            results.append({
                "line": idx,
                "input": line,
                "status": "error",
                "error": "service_id and quantity must be valid numbers"
            })
            fail_count += 1
            continue

        try:
            order_res = execute_single_order(
                service_id=service_id,
                link=link,
                quantity=quantity,
                current_user=current_user,
                db=db
            )
            results.append({
                "line": idx,
                "status": "success",
                "order_id": order_res["order_id"],
                "charge": order_res["charge"],
                "service": order_res["service_name"]
            })
            success_count += 1
        except HTTPException as he:
            results.append({
                "line": idx,
                "input": line,
                "status": "error",
                "error": he.detail
            })
            fail_count += 1
        except Exception as ex:
            results.append({
                "line": idx,
                "input": line,
                "status": "error",
                "error": str(ex)
            })
            fail_count += 1

    return {
        "success": True,
        "total": len(lines),
        "success_count": success_count,
        "fail_count": fail_count,
        "results": results
    }


@router.get("")
def list_orders(
    status_filter: Optional[str] = Query(None, alias="status"),
    search: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    stmt = select(Order).where(Order.user_id == current_user.id)

    if status_filter and status_filter.lower() != "all":
        stmt = stmt.where(func.lower(Order.status) == status_filter.lower())

    if search and search.strip():
        search_term = f"%{search.strip().lower()}%"
        stmt = stmt.where(
            func.lower(Order.link).like(search_term)
            | func.cast(Order.id, func.TEXT).like(search_term)
        )

    stmt = stmt.order_by(Order.created_at.desc()).limit(limit).offset(offset)
    orders = db.scalars(stmt).all()

    return {
        "success": True,
        "count": len(orders),
        "orders": [
            {
                "id": o.id,
                "service_id": o.service_id,
                "service_name": o.service.name if o.service else "Service",
                "link": o.link,
                "quantity": o.quantity,
                "charge": str(o.charge),
                "start_count": o.start_count,
                "remains": o.remains,
                "status": o.status,
                "provider_slug": o.snapshot_provider_slug,
                "refill_available": o.service.refill_available if o.service else False,
                "refill_status": o.refill_status,
                "cancel_available": o.service.cancel_available if o.service else False,
                "created_at": o.created_at
            }
            for o in orders
        ]
    }


@router.get("/{order_id}/status")
def get_order_status(
    order_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    order = db.scalar(
        select(Order).where(
            Order.id == order_id,
            Order.user_id == current_user.id
        )
    )

    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    if not order.provider_order_id:
        return {
            "success": True,
            "order_id": order.id,
            "status": order.status,
            "start_count": order.start_count,
            "remains": order.remains
        }

    slug = order.snapshot_provider_slug or (order.service.provider_name if order.service else "vipsmmpro")
    provider_setting = db.scalar(
        select(ProviderSetting).where(
            ProviderSetting.slug == slug,
            ProviderSetting.is_active == True,  # noqa: E712
        )
    )
    if provider_setting:
        try:
            adapter = build_adapter(provider_setting)
            ps = adapter.get_order_status(str(order.provider_order_id))
            if not ps.error:
                order.status = ps.status
                if ps.start_count is not None:
                    order.start_count = ps.start_count
                if ps.remains is not None:
                    order.remains = ps.remains
                if ps.charge is not None:
                    order.provider_charge = ps.charge
                db.commit()
        except Exception:
            pass

    return {
        "success": True,
        "order_id": order.id,
        "status": order.status,
        "start_count": order.start_count,
        "remains": order.remains,
        "charge": str(order.charge)
    }


@router.post("/{order_id}/refill")
def refill_order(
    order_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    order = db.scalar(
        select(Order).where(
            Order.id == order_id,
            Order.user_id == current_user.id
        )
    )

    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    if not order.provider_order_id:
        raise HTTPException(status_code=400, detail="Provider order ID is missing")

    service = db.get(Service, order.service_id)
    if not service or not service.refill_available:
        raise HTTPException(status_code=400, detail="Refill is not available for this service")

    if order.refill_id:
        raise HTTPException(status_code=400, detail="Refill has already been requested")

    slug = order.snapshot_provider_slug or (order.service.provider_name if order.service else "vipsmmpro")
    provider_setting = db.scalar(
        select(ProviderSetting).where(
            ProviderSetting.slug == slug,
            ProviderSetting.is_active == True,  # noqa: E712
        )
    )
    if not provider_setting:
        raise HTTPException(status_code=503, detail="Provider not available for refill")

    try:
        adapter = build_adapter(provider_setting)
        refill_id_str = adapter.refill_order(str(order.provider_order_id))
    except ProviderError as error:
        raise HTTPException(status_code=400, detail=str(error))
    except Exception as error:
        raise HTTPException(status_code=502, detail=f"Provider request failed: {error}")

    order.refill_id = int(refill_id_str) if refill_id_str.isdigit() else None
    order.refill_status = "Pending"
    db.commit()

    return {
        "success": True,
        "order_id": order.id,
        "refill_id": order.refill_id,
        "status": order.refill_status
    }


@router.post("/{order_id}/cancel")
def cancel_order(
    order_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    order = db.scalar(
        select(Order).where(
            Order.id == order_id,
            Order.user_id == current_user.id
        )
    )

    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    if not order.provider_order_id:
        raise HTTPException(status_code=400, detail="Provider order ID is missing")

    service = db.get(Service, order.service_id)
    if not service or not service.cancel_available:
        raise HTTPException(status_code=400, detail="Cancel is not available for this service")

    if order.status in {"Completed", "Canceled", "Cancelled"}:
        raise HTTPException(status_code=400, detail="Order cannot be canceled")

    slug = order.snapshot_provider_slug or (order.service.provider_name if order.service else "vipsmmpro")
    provider_setting = db.scalar(
        select(ProviderSetting).where(
            ProviderSetting.slug == slug,
            ProviderSetting.is_active == True,  # noqa: E712
        )
    )
    if not provider_setting:
        raise HTTPException(status_code=503, detail="Provider not available for cancel")

    try:
        adapter = build_adapter(provider_setting)
        adapter.cancel_orders([str(order.provider_order_id)])
    except ProviderError as error:
        raise HTTPException(status_code=400, detail=str(error))
    except Exception as error:
        raise HTTPException(status_code=502, detail=f"Provider request failed: {error}")

    order.status = "Canceled"
    db.commit()

    return {
        "success": True,
        "order_id": order.id,
        "status": order.status
    }


@router.post("/sync-statuses")
def sync_statuses(
    admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    result = sync_order_statuses(db)
    return {
        "success": True,
        **result
    }