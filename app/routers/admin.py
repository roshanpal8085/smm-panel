"""
Admin router — platform management, user controls, deposit approval/rejection.
All deposit state transitions use process_deposit_completion() and
process_deposit_rejection() from app/services/deposit.py which enforce
row-level locking for exactly-once wallet credit guarantees.
"""
from datetime import datetime
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import (
    Order,
    PaymentTransaction,
    ProviderSetting,
    Service,
    SupportTicket,
    SystemSetting,
    TicketMessage,
    Transaction,
    User,
)
from app.pricing import calculate_selling_rate
from app.providers.registry import build_adapter
from app.security import get_current_admin
from app.services.deposit import process_deposit_completion, process_deposit_rejection
from app.services.wallet import credit_wallet, debit_wallet

router = APIRouter(
    prefix="/admin",
    tags=["Admin Management"],
    dependencies=[Depends(get_current_admin)],
)


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------

class BalanceAdjustRequest(BaseModel):
    amount: Decimal = Field(gt=0)
    action: str = Field(pattern="^(add|deduct)$")
    reason: str = Field(min_length=3)


class SettingsUpdateRequest(BaseModel):
    site_name: Optional[str] = None
    currency: Optional[str] = None
    currency_symbol: Optional[str] = None
    upi_id: Optional[str] = None
    announcement: Optional[str] = None
    default_markup_percent: Optional[str] = None
    min_deposit: Optional[str] = None
    max_upi_deposit: Optional[str] = None


class TicketStatusRequest(BaseModel):
    status: str = Field(pattern="^(Open|Answered|Closed)$")


class DepositApproveRequest(BaseModel):
    admin_notes: Optional[str] = None


class DepositRejectRequest(BaseModel):
    reason: str = Field(min_length=3, max_length=500)


# ---------------------------------------------------------------------------
# GET /admin/stats
# ---------------------------------------------------------------------------

@router.get("/stats")
def get_admin_stats(db: Session = Depends(get_db)):
    total_users = db.scalar(select(func.count(User.id))) or 0
    total_orders = db.scalar(select(func.count(Order.id))) or 0
    total_revenue = db.scalar(select(func.sum(Order.charge))) or Decimal("0.00")

    pending_deposits = db.scalar(
        select(func.count(PaymentTransaction.id)).where(
            PaymentTransaction.status.in_(["Pending", "Pending Verification"])
        )
    ) or 0

    open_tickets = db.scalar(
        select(func.count(SupportTicket.id)).where(
            SupportTicket.status.in_(["Open", "Answered"])
        )
    ) or 0

    # Profit Calculations
    total_gross_profit = db.scalar(
        select(func.sum(Order.gross_profit)).where(
            Order.status.in_(["Completed", "Processing", "In progress", "Pending"])
        )
    ) or Decimal("0.00")

    realized_profit = db.scalar(
        select(func.sum(Order.gross_profit)).where(
            Order.status == "Completed"
        )
    ) or Decimal("0.00")

    total_services = db.scalar(select(func.count(Service.id))) or 0
    active_services = db.scalar(select(func.count(Service.id)).where(Service.is_active == True, Service.customer_visible == True)) or 0

    # Live provider balances (all active providers)
    provider_balances: list[dict] = []
    active_providers = db.scalars(
        select(ProviderSetting).where(ProviderSetting.is_active == True)  # noqa: E712
    ).all()
    for _p in active_providers:
        try:
            _adapter = build_adapter(_p)
            _bal = _adapter.get_balance()
            provider_balances.append(
                {"slug": _p.slug, "name": _p.name, "balance": str(_bal)}
            )
        except Exception:
            provider_balances.append(
                {"slug": _p.slug, "name": _p.name, "balance": "N/A"}
            )
    # Legacy single-provider compat
    provider_balance = provider_balances[0]["balance"] if provider_balances else "N/A"
    provider_currency = "INR"

    return {
        "success": True,
        "stats": {
            "total_users": total_users,
            "total_orders": total_orders,
            "total_revenue": str(total_revenue),
            "pending_deposits": pending_deposits,
            "open_tickets": open_tickets,
            "total_gross_profit": str(total_gross_profit),
            "realized_profit": str(realized_profit),
            "total_services": total_services,
            "active_services": active_services,
            "provider_balance": provider_balance,
            "provider_currency": provider_currency,
            "provider_balances": provider_balances,
        },
    }


# ---------------------------------------------------------------------------
# GET /admin/users
# ---------------------------------------------------------------------------

@router.get("/users")
def get_admin_users(
    search: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    db: Session = Depends(get_db)
):
    stmt = select(User)
    if search and search.strip():
        term = f"%{search.strip().lower()}%"
        stmt = stmt.where(
            func.lower(User.username).like(term)
            | func.lower(User.email).like(term)
        )

    stmt = stmt.order_by(User.id.desc()).limit(limit).offset(offset)
    users = db.scalars(stmt).all()

    return {
        "success": True,
        "count": len(users),
        "users": [
            {
                "id": u.id,
                "username": u.username,
                "email": u.email,
                "balance": str(u.balance),
                "is_active": u.is_active,
                "is_admin": u.is_admin,
                "can_manual_upi": u.can_manual_upi,
                "api_key": u.api_key,
                "orders_count": len(u.orders),
                "created_at": u.created_at,
            }
            for u in users
        ],
    }


# ---------------------------------------------------------------------------
# POST /admin/users/{user_id}/balance
# ---------------------------------------------------------------------------

@router.post("/users/{user_id}/balance")
def adjust_user_balance(
    user_id: int,
    data: BalanceAdjustRequest,
    admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    target_user = db.get(User, user_id)
    if not target_user:
        raise HTTPException(status_code=404, detail="User not found")

    if data.action == "add":
        credit_wallet(
            db=db,
            user_id=target_user.id,
            amount=data.amount,
            transaction_type="admin_credit",
            description=f"Admin credit: {data.reason} (by {admin.username})",
        )
    else:
        debit_wallet(
            db=db,
            user_id=target_user.id,
            amount=data.amount,
            description=f"Admin debit: {data.reason} (by {admin.username})",
        )

    db.commit()
    db.refresh(target_user)

    return {
        "success": True,
        "message": f"Successfully updated balance for {target_user.username}",
        "new_balance": str(target_user.balance),
    }


# ---------------------------------------------------------------------------
# POST /admin/users/{user_id}/toggle-active
# ---------------------------------------------------------------------------

@router.post("/users/{user_id}/toggle-active")
def toggle_user_active(
    user_id: int,
    admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    target_user = db.get(User, user_id)
    if not target_user:
        raise HTTPException(status_code=404, detail="User not found")
    if target_user.id == admin.id:
        raise HTTPException(status_code=400, detail="Cannot disable your own admin account")

    target_user.is_active = not target_user.is_active
    db.commit()

    return {"success": True, "is_active": target_user.is_active}


# ---------------------------------------------------------------------------
# POST /admin/users/{user_id}/toggle-admin
# ---------------------------------------------------------------------------

@router.post("/users/{user_id}/toggle-admin")
def toggle_user_admin(
    user_id: int,
    admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    target_user = db.get(User, user_id)
    if not target_user:
        raise HTTPException(status_code=404, detail="User not found")
    if target_user.id == admin.id:
        raise HTTPException(status_code=400, detail="Cannot modify your own admin privileges")

    target_user.is_admin = not target_user.is_admin
    db.commit()

    return {"success": True, "is_admin": target_user.is_admin}


# ---------------------------------------------------------------------------
# POST /admin/users/{user_id}/toggle-upi
# Enables or disables manual UPI deposit access for a user.
# ---------------------------------------------------------------------------

@router.post("/users/{user_id}/toggle-upi")
def toggle_user_upi_access(
    user_id: int,
    admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    target_user = db.get(User, user_id)
    if not target_user:
        raise HTTPException(status_code=404, detail="User not found")

    target_user.can_manual_upi = not target_user.can_manual_upi
    db.commit()

    return {
        "success": True,
        "can_manual_upi": target_user.can_manual_upi,
        "message": f"Manual UPI {'enabled' if target_user.can_manual_upi else 'disabled'} for {target_user.username}",
    }


# ---------------------------------------------------------------------------
# GET /admin/orders
# ---------------------------------------------------------------------------

@router.get("/orders")
def get_admin_orders(
    status_filter: Optional[str] = Query(None, alias="status"),
    search: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
    db: Session = Depends(get_db)
):
    stmt = select(Order)
    if status_filter and status_filter.lower() != "all":
        stmt = stmt.where(func.lower(Order.status) == status_filter.lower())

    if search and search.strip():
        term = f"%{search.strip().lower()}%"
        stmt = stmt.where(
            func.lower(Order.link).like(term)
            | func.cast(Order.id, func.TEXT).like(term)
            | func.cast(Order.provider_order_id, func.TEXT).like(term)
        )

    stmt = stmt.order_by(Order.created_at.desc()).limit(limit).offset(offset)
    orders = db.scalars(stmt).all()

    return {
        "success": True,
        "count": len(orders),
        "orders": [
            {
                "id": o.id,
                "user_id": o.user_id,
                "username": o.user.username if o.user else "N/A",
                "service_id": o.service_id,
                "service_name": o.service.name if o.service else "Service",
                "provider_order_id": o.provider_order_id,
                "link": o.link,
                "quantity": o.quantity,
                "charge": str(o.charge),
                "start_count": o.start_count,
                "remains": o.remains,
                "status": o.status,
                "created_at": o.created_at,
            }
            for o in orders
        ],
    }


# ---------------------------------------------------------------------------
# GET /admin/deposits
# ---------------------------------------------------------------------------

@router.get("/deposits")
def get_admin_deposits(
    status_filter: Optional[str] = Query(None, alias="status"),
    limit: int = 50,
    offset: int = 0,
    db: Session = Depends(get_db)
):
    stmt = select(PaymentTransaction)
    if status_filter and status_filter.lower() != "all":
        stmt = stmt.where(PaymentTransaction.status == status_filter)

    stmt = stmt.order_by(PaymentTransaction.created_at.desc()).limit(limit).offset(offset)
    deposits = db.scalars(stmt).all()

    return {
        "success": True,
        "count": len(deposits),
        "deposits": [
            {
                "id": d.id,
                "user_id": d.user_id,
                "username": d.user.username if d.user else "User",
                "reference": d.reference,
                "amount": str(d.amount),
                "currency": d.currency,
                "status": d.status,
                "utr_number": d.utr_number,
                "payment_method": d.payment_method or d.gateway or "UPI",
                "notes": d.notes,
                "admin_notes": d.admin_notes,
                "screenshot_path": d.screenshot_path,
                "created_at": d.created_at,
                "completed_at": d.completed_at,
            }
            for d in deposits
        ],
    }


# ---------------------------------------------------------------------------
# POST /admin/deposits/{payment_id}/approve
# Uses process_deposit_completion() with row-level locking — idempotent.
# ---------------------------------------------------------------------------

@router.post("/deposits/{payment_id}/approve")
def approve_deposit(
    payment_id: int,
    data: DepositApproveRequest = DepositApproveRequest(),
    admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    deposit = process_deposit_completion(
        db=db,
        deposit_id=payment_id,
        admin_user=admin,
        admin_notes=data.admin_notes or f"Approved by {admin.username}",
    )

    return {
        "success": True,
        "message": f"Payment #{deposit.id} (₹{deposit.amount}) approved and credited to user.",
    }


# ---------------------------------------------------------------------------
# POST /admin/deposits/{payment_id}/reject
# Uses process_deposit_rejection() with row-level locking. No wallet credit.
# ---------------------------------------------------------------------------

@router.post("/deposits/{payment_id}/reject")
def reject_deposit(
    payment_id: int,
    data: DepositRejectRequest,
    admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    deposit = process_deposit_rejection(
        db=db,
        deposit_id=payment_id,
        admin_user=admin,
        reason=data.reason,
    )

    return {
        "success": True,
        "message": f"Payment #{deposit.id} rejected. Reason recorded.",
    }


# ---------------------------------------------------------------------------
# GET /admin/tickets
# ---------------------------------------------------------------------------

@router.get("/tickets")
def get_admin_tickets(
    status_filter: Optional[str] = Query(None, alias="status"),
    db: Session = Depends(get_db)
):
    stmt = select(SupportTicket)
    if status_filter and status_filter.lower() != "all":
        stmt = stmt.where(SupportTicket.status == status_filter)

    stmt = stmt.order_by(SupportTicket.updated_at.desc())
    tickets = db.scalars(stmt).all()

    return {
        "success": True,
        "count": len(tickets),
        "tickets": [
            {
                "id": t.id,
                "user_id": t.user_id,
                "username": t.user.username if t.user else "User",
                "subject": t.subject,
                "category": t.category,
                "order_id": t.order_id,
                "status": t.status,
                "priority": t.priority,
                "created_at": t.created_at,
                "updated_at": t.updated_at,
                "message_count": len(t.messages),
            }
            for t in tickets
        ],
    }


# ---------------------------------------------------------------------------
# GET /admin/settings
# ---------------------------------------------------------------------------

@router.get("/settings")
def get_settings(db: Session = Depends(get_db)):
    settings = db.scalars(select(SystemSetting)).all()
    return {"success": True, "settings": {s.key: s.value for s in settings}}


# ---------------------------------------------------------------------------
# POST /admin/settings
# ---------------------------------------------------------------------------

@router.post("/settings")
def update_settings(
    data: SettingsUpdateRequest,
    db: Session = Depends(get_db)
):
    updates = data.model_dump(exclude_unset=True)
    for key, value in updates.items():
        if value is not None:
            setting = db.scalar(select(SystemSetting).where(SystemSetting.key == key))
            if setting:
                setting.value = str(value)
            else:
                db.add(SystemSetting(key=key, value=str(value)))

    db.commit()
    return {"success": True, "message": "Settings updated successfully"}
