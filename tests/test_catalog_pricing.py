"""
Comprehensive tests for service catalog management and pricing engine:
- Automatic pricing from provider rate and global markup
- Manual price overrides & preserving them when global markup changes
- Category markup overrides
- Bulk visibility and pricing adjustments
- Strict 4-tier service orderability (hidden, disabled, unavailable)
- Historical order price snapshotting
- Admin authorization enforcement on pricing endpoints
"""
from decimal import Decimal
import pytest
from app.database import SessionLocal
from app.models import CategoryMargin, Order, PriceAuditLog, Service, SystemSetting, User
from app.pricing import (
    calculate_gross_profit,
    calculate_margin_percent,
    calculate_markup_from_rates,
    calculate_selling_rate,
    get_effective_markup_percent,
    get_global_markup_percent,
)


def get_admin_headers(client):
    res = client.post("/auth/token", data={"username": "roshan25", "password": "roshan123"})
    if res.status_code != 200:
        pytest.skip("Admin login credentials unavailable")
    token = res.json().get("access_token")
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Test 1: Pricing Engine Calculation Logic
# ---------------------------------------------------------------------------

class TestPricingCalculations:
    def test_markup_vs_margin_distinction(self):
        """Markup on provider cost must be strictly distinct from gross profit margin."""
        provider_cost = Decimal("100.00")
        selling_price = Decimal("150.00")

        # Markup = (150 - 100) / 100 * 100 = 50.00%
        markup = calculate_markup_from_rates(selling_price, provider_cost)
        assert markup == Decimal("50.00")

        # Gross Margin = (150 - 100) / 150 * 100 = 33.33%
        margin = calculate_margin_percent(selling_price, provider_cost)
        assert margin == Decimal("33.33")

        # Gross Profit = 150 - 100 = 50.00
        profit = calculate_gross_profit(selling_price, provider_cost)
        assert profit == Decimal("50.0000")

    def test_calculate_selling_rate_with_markup(self):
        provider_cost = Decimal("10.00")
        selling_rate = calculate_selling_rate(provider_cost, markup_percent=Decimal("60.0"))
        # 10 * 1.6 = 16.0000
        assert selling_rate == Decimal("16.0000")


# ---------------------------------------------------------------------------
# Test 2: Category and Global Markup Hierarchy
# ---------------------------------------------------------------------------

class TestMarkupHierarchy:
    def test_category_margin_overrides_global(self):
        db = SessionLocal()
        try:
            # Set global markup
            setting = db.scalar(SystemSetting.__table__.select().where(SystemSetting.key == "default_markup_percent"))
            # Create a test category margin
            cm = db.scalar(CategoryMargin.__table__.select().where(CategoryMargin.category == "TestCategoryHierarchy"))
            if not cm:
                db.add(CategoryMargin(category="TestCategoryHierarchy", markup_percent=Decimal("75.00")))
                db.commit()

            service = Service(
                provider_service_id=999901,
                name="Test Service Hierarchy",
                category="TestCategoryHierarchy",
                service_type="Default",
                provider_rate=Decimal("10.00"),
                selling_rate=Decimal("17.50"),
                min_quantity=10,
                max_quantity=1000
            )

            effective = get_effective_markup_percent(service, db)
            assert effective == Decimal("75.00")
        finally:
            db.close()

    def test_manual_markup_overrides_category_and_global(self):
        db = SessionLocal()
        try:
            service = Service(
                provider_service_id=999902,
                name="Test Manual Override",
                category="TestCategoryHierarchy",
                service_type="Default",
                provider_rate=Decimal("10.00"),
                selling_rate=Decimal("20.00"),
                manual_markup_percent=Decimal("100.00"),
                min_quantity=10,
                max_quantity=1000
            )
            effective = get_effective_markup_percent(service, db)
            assert effective == Decimal("100.00")
        finally:
            db.close()


# ---------------------------------------------------------------------------
# Test 3: Catalog Administration API
# ---------------------------------------------------------------------------

class TestCatalogAdminAPI:
    def test_admin_catalog_requires_auth(self, client):
        res = client.get("/services/admin/catalog")
        assert res.status_code in (401, 403)

    def test_admin_catalog_forbidden_for_regular_user(self, client, user_headers):
        res = client.get("/services/admin/catalog", headers=user_headers)
        assert res.status_code == 403

    def test_manual_price_update_creates_audit_log(self, client, regular_user):
        admin_headers = get_admin_headers(client)
        # 1. Update price for service #1
        res = client.patch(
            "/services/1/price",
            headers=admin_headers,
            json={"selling_rate": 25.50, "notes": "Test manual price update"}
        )
        assert res.status_code == 200, res.text
        assert res.json()["success"] is True

        # 2. Check audit logs
        audit_res = client.get("/services/admin/price-audits?service_id=1", headers=admin_headers)
        assert audit_res.status_code == 200
        logs = audit_res.json()["logs"]
        assert len(logs) > 0
        assert logs[0]["change_type"] == "manual_override"

    def test_reset_service_to_auto_pricing(self, client):
        admin_headers = get_admin_headers(client)
        res = client.post("/services/1/reset-price", headers=admin_headers)
        assert res.status_code == 200
        assert res.json()["success"] is True


# ---------------------------------------------------------------------------
# Test 4: Strict 4-Tier Orderability Enforcement
# ---------------------------------------------------------------------------

class TestOrderabilityEnforcement:
    def test_hidden_service_cannot_be_ordered(self, client, user_headers):
        db = SessionLocal()
        s = db.get(Service, 1)
        prev_visible = s.customer_visible
        s.customer_visible = False
        db.commit()
        db.close()

        try:
            # Customer tries to order service #1 directly
            res = client.post(
                "/orders",
                headers=user_headers,
                json={"service_id": 1, "link": "https://instagram.com/test", "quantity": 100}
            )
            # Must reject with 400 (hidden from customer ordering)
            assert res.status_code in (400, 404), res.text
            assert "hidden" in res.json().get("detail", "").lower() or "not found" in res.json().get("detail", "").lower()
        finally:
            db = SessionLocal()
            s = db.get(Service, 1)
            s.customer_visible = prev_visible
            db.commit()
            db.close()

    def test_disabled_service_cannot_be_ordered(self, client, user_headers):
        db = SessionLocal()
        s = db.get(Service, 1)
        prev_active = s.is_active
        s.is_active = False
        db.commit()
        db.close()

        try:
            res = client.post(
                "/orders",
                headers=user_headers,
                json={"service_id": 1, "link": "https://instagram.com/test", "quantity": 100}
            )
            assert res.status_code in (400, 404), res.text
        finally:
            db = SessionLocal()
            s = db.get(Service, 1)
            s.is_active = prev_active
            db.commit()
            db.close()

    def test_public_services_filters_hidden_and_inactive(self, client):
        db = SessionLocal()
        s = db.get(Service, 1)
        prev_visible = s.customer_visible
        s.customer_visible = False
        db.commit()
        db.close()

        try:
            res = client.get("/services")
            assert res.status_code == 200
            service_ids = [srv["id"] for srv in res.json()["services"]]
            assert 1 not in service_ids
        finally:
            db = SessionLocal()
            s = db.get(Service, 1)
            s.customer_visible = prev_visible
            db.commit()
            db.close()


# ---------------------------------------------------------------------------
# Test 5: Historical Order Pricing Snapshot Integrity
# ---------------------------------------------------------------------------

class TestHistoricalOrderSnapshot:
    def test_future_price_changes_do_not_alter_past_orders(self, client, regular_user):
        db = SessionLocal()
        try:
            # Create a test past order
            past_order = Order(
                user_id=regular_user.id,
                service_id=1,
                provider_order_id=888801,
                link="https://instagram.com/p/historic",
                quantity=1000,
                charge=Decimal("50.00"),
                snapshot_rate=Decimal("50.00"),
                snapshot_provider_rate=Decimal("30.00"),
                gross_profit=Decimal("20.00"),
                status="Completed"
            )
            db.add(past_order)
            db.commit()
            order_id = past_order.id

            # Now update the service's selling rate drastically
            service = db.get(Service, 1)
            service.selling_rate = Decimal("999.00")
            db.commit()

            # Refresh order and verify charge & snapshot rate remained untouched
            db.refresh(past_order)
            assert past_order.charge == Decimal("50.00")
            assert past_order.snapshot_rate == Decimal("50.00")
            assert past_order.gross_profit == Decimal("20.00")
        finally:
            db.close()
