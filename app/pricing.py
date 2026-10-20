"""
Pricing engine: handles global default markup, category-level overrides,
per-service manual markups, manual price overrides, profit calculations,
and distinguishes markup on provider cost from gross profit margin.
All settings are persisted in the database.
"""
import os
from decimal import Decimal
from typing import Optional
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import CategoryMargin, Service, SystemSetting


def get_global_markup_percent(db: Optional[Session] = None) -> Decimal:
    """Fetches global default markup percentage from database, fallback to env or 50%."""
    if db:
        val = db.scalar(select(SystemSetting.value).where(SystemSetting.key == "default_markup_percent"))
        if val is not None:
            try:
                return Decimal(str(val))
            except Exception:
                pass
    env_val = os.getenv("DEFAULT_MARKUP_PERCENT", "50")
    try:
        return Decimal(env_val)
    except Exception:
        return Decimal("50.00")


def get_effective_markup_percent(
    service: Service,
    db: Optional[Session] = None
) -> Decimal:
    """
    Returns the effective markup percentage for a service:
    1. Service-level manual markup (if set)
    2. Category-level markup (if set in CategoryMargin)
    3. Global default markup (from system_settings)
    """
    if service.manual_markup_percent is not None:
        return Decimal(str(service.manual_markup_percent))

    if db and service.category:
        cat_margin = db.scalar(
            select(CategoryMargin.markup_percent).where(CategoryMargin.category == service.category)
        )
        if cat_margin is not None:
            return Decimal(str(cat_margin))

    return get_global_markup_percent(db)


def calculate_selling_rate(
    provider_rate: Decimal,
    markup_percent: Optional[Decimal] = None,
    service: Optional[Service] = None,
    db: Optional[Session] = None
) -> Decimal:
    """
    Calculates selling price from provider cost based on markup:
    Selling Price = Provider Rate * (1 + markup_percent / 100)
    """
    if markup_percent is None:
        if service is not None:
            markup_percent = get_effective_markup_percent(service, db)
        else:
            markup_percent = get_global_markup_percent(db)

    multiplier = Decimal("1") + (Decimal(str(markup_percent)) / Decimal("100"))
    selling_rate = provider_rate * multiplier
    return selling_rate.quantize(Decimal("0.0001"))


def calculate_gross_profit(selling_rate: Decimal, provider_rate: Decimal) -> Decimal:
    """Gross profit per 1,000 units = selling_rate - provider_rate."""
    return (selling_rate - provider_rate).quantize(Decimal("0.0001"))


def calculate_margin_percent(selling_rate: Decimal, provider_rate: Decimal) -> Decimal:
    """
    Gross Profit Margin % = (Selling Price - Provider Cost) / Selling Price * 100
    Distinct from Markup % = (Selling Price - Provider Cost) / Provider Cost * 100
    """
    if selling_rate <= Decimal("0"):
        return Decimal("0.00")
    profit = selling_rate - provider_rate
    margin = (profit / selling_rate) * Decimal("100")
    return margin.quantize(Decimal("0.01"))


def calculate_markup_from_rates(selling_rate: Decimal, provider_rate: Decimal) -> Decimal:
    """
    Markup % = (Selling Price - Provider Cost) / Provider Cost * 100
    """
    if provider_rate <= Decimal("0"):
        return Decimal("0.00")
    markup = ((selling_rate - provider_rate) / provider_rate) * Decimal("100")
    return markup.quantize(Decimal("0.01"))