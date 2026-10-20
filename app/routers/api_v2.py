from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Order, Service, User
from app.routers.orders import execute_single_order
from app.security import get_user_by_api_key

router = APIRouter(
    prefix="/api/v2",
    tags=["Reseller API v2"]
)


@router.api_route("", methods=["GET", "POST"])
async def reseller_api_v2_handler(
    request: Request,
    db: Session = Depends(get_db)
):
    # Extract query params and form data safely
    if request.method == "POST":
        try:
            form_data = await request.form()
            params = dict(form_data)
        except Exception:
            params = {}
        # Also merge query params if any
        for k, v in request.query_params.items():
            if k not in params:
                params[k] = v
    else:
        params = dict(request.query_params)

    api_key = params.get("key")
    if not api_key:
        return {"error": "API key is missing"}

    user = get_user_by_api_key(api_key.strip(), db)
    if not user:
        return {"error": "Invalid API key or account is disabled"}

    action = params.get("action")
    if not action:
        return {"error": "Action is missing"}

    action = action.strip().lower()

    # 1. Services
    if action == "services":
        services = db.scalars(
            select(Service)
            .where(Service.is_active == True)
            .order_by(Service.category, Service.id)
        ).all()

        return [
            {
                "service": s.id,
                "name": s.name,
                "type": s.service_type,
                "category": s.category,
                "rate": str(s.selling_rate),
                "min": s.min_quantity,
                "max": s.max_quantity,
                "refill": s.refill_available,
                "cancel": s.cancel_available
            }
            for s in services
        ]

    # 2. Balance
    elif action == "balance":
        return {
            "balance": str(user.balance),
            "currency": "INR"
        }

    # 3. Add Order
    elif action == "add":
        service_id = params.get("service")
        link = params.get("link")
        quantity = params.get("quantity")

        if not service_id or not link or not quantity:
            return {"error": "service, link, and quantity parameters are required"}

        try:
            service_id = int(service_id)
            quantity = int(quantity)
        except ValueError:
            return {"error": "service and quantity must be valid integers"}

        try:
            order_res = execute_single_order(
                service_id=service_id,
                link=link,
                quantity=quantity,
                current_user=user,
                db=db
            )
            return {"order": order_res["order_id"]}
        except HTTPException as he:
            return {"error": str(he.detail)}
        except Exception as ex:
            return {"error": str(ex)}

    # 4. Order Status (single or multiple)
    elif action == "status":
        order_param = params.get("order")
        orders_param = params.get("orders")

        if order_param:
            try:
                order_id = int(order_param)
            except ValueError:
                return {"error": "Invalid order ID"}

            order = db.scalar(
                select(Order).where(
                    Order.id == order_id,
                    Order.user_id == user.id
                )
            )
            if not order:
                return {"error": "Order not found"}

            return {
                "charge": str(order.charge),
                "start_count": str(order.start_count) if order.start_count is not None else None,
                "status": order.status,
                "remains": str(order.remains) if order.remains is not None else None,
                "currency": "INR"
            }

        elif orders_param:
            raw_ids = [s.strip() for s in orders_param.split(",") if s.strip()]
            order_ids = []
            for r in raw_ids:
                try:
                    order_ids.append(int(r))
                except ValueError:
                    continue

            orders = db.scalars(
                select(Order).where(
                    Order.id.in_(order_ids),
                    Order.user_id == user.id
                )
            ).all()

            result = {}
            for o in orders:
                result[str(o.id)] = {
                    "charge": str(o.charge),
                    "start_count": str(o.start_count) if o.start_count is not None else None,
                    "status": o.status,
                    "remains": str(o.remains) if o.remains is not None else None,
                    "currency": "INR"
                }

            # Fill missing orders
            for oid in order_ids:
                if str(oid) not in result:
                    result[str(oid)] = {"error": "Incorrect order ID"}

            return result

        else:
            return {"error": "order or orders parameter is required"}

    # 5. Refill
    elif action == "refill":
        order_param = params.get("order")
        if not order_param:
            return {"error": "order parameter is required"}
        try:
            order_id = int(order_param)
        except ValueError:
            return {"error": "Invalid order ID"}

        order = db.scalar(
            select(Order).where(
                Order.id == order_id,
                Order.user_id == user.id
            )
        )
        if not order:
            return {"error": "Order not found"}

        if not order.service.refill_available:
            return {"error": "Refill not available for this service"}

        # Provider refill
        from app.providers.vipsmmpro import VipSmmProClient
        provider = VipSmmProClient()
        try:
            res = provider.refill_order(order.provider_order_id)
            if isinstance(res, dict) and "refill" in res:
                order.refill_id = int(res["refill"])
                order.refill_status = "Pending"
                db.commit()
                return {"refill": order.refill_id}
            else:
                return {"error": res.get("error", "Refill rejected by provider") if isinstance(res, dict) else "Provider error"}
        except Exception as e:
            return {"error": str(e)}

    # 6. Refill status
    elif action == "refill_status":
        refill_param = params.get("refill")
        if not refill_param:
            return {"error": "refill parameter is required"}
        try:
            refill_id = int(refill_param)
        except ValueError:
            return {"error": "Invalid refill ID"}

        order = db.scalar(
            select(Order).where(
                Order.refill_id == refill_id,
                Order.user_id == user.id
            )
        )
        if not order:
            return {"error": "Refill not found"}

        return {"status": order.refill_status or "Pending"}

    return {"error": f"Unknown action '{action}'"}
