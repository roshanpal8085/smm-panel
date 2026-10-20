from decimal import Decimal
from unittest.mock import MagicMock, patch
import pytest

from app.database import SessionLocal
from app.models import Order, ProviderSetting, ProviderSyncLog, Service, User
from app.providers.base import (
    ProviderConfig,
    ProviderError,
    ProviderOrderResult,
    ProviderOrderStatus,
    ProviderService,
    SmmProviderAdapter,
)
from app.providers.registry import (
    build_adapter,
    get_adapter_class,
    list_registered_slugs,
    register_provider,
)
from app.services.provider_sync import (
    import_services_from_all_providers,
    import_services_from_provider,
)
from app.services.sync import sync_order_statuses


# ---------------------------------------------------------------------------
# Mock Adapter for Testing
# ---------------------------------------------------------------------------

class MockSmmAdapter(SmmProviderAdapter):
    """Test adapter with deterministic mock responses."""

    def get_services(self) -> list[ProviderService]:
        return [
            ProviderService(
                provider_service_id="101",
                name="Mock Instagram Followers",
                category="Instagram",
                service_type="Default",
                rate=Decimal("1.5000"),
                min_quantity=100,
                max_quantity=10000,
                refill=True,
                cancel=True,
            ),
            ProviderService(
                provider_service_id="102",
                name="Mock YouTube Views",
                category="YouTube",
                service_type="Default",
                rate=Decimal("2.0000"),
                min_quantity=500,
                max_quantity=50000,
                refill=False,
                cancel=False,
            ),
        ]

    def get_balance(self) -> Decimal:
        return Decimal("150.75")

    def add_order(self, provider_service_id: str, link: str, quantity: int) -> ProviderOrderResult:
        if provider_service_id == "fail_order":
            raise ProviderError("Service unavailable at provider")
        return ProviderOrderResult(
            provider_order_id="99901",
            raw_response={"order": 99901},
        )

    def get_order_status(self, provider_order_id: str) -> ProviderOrderStatus:
        return ProviderOrderStatus(
            provider_order_id=provider_order_id,
            status="In progress",
            start_count=10,
            remains=50,
            charge=Decimal("1.25"),
        )

    def get_multiple_order_statuses(self, provider_order_ids: list[str]) -> list[ProviderOrderStatus]:
        return [
            ProviderOrderStatus(
                provider_order_id=oid,
                status="Completed",
                start_count=100,
                remains=0,
                charge=Decimal("1.50"),
            )
            for oid in provider_order_ids
        ]

    def refill_order(self, provider_order_id: str) -> str:
        return "refill_888"

    def get_refill_status(self, refill_id: str) -> str:
        return "Completed"

    def cancel_orders(self, provider_order_ids: list[str]):
        return {oid: "Canceled" for oid in provider_order_ids}


@pytest.fixture(autouse=True)
def setup_mock_provider():
    """Register mock provider and clean up test records."""
    register_provider("mock_provider", MockSmmAdapter)
    db = SessionLocal()
    # Ensure provider setting exists for testing
    setting = db.query(ProviderSetting).filter(ProviderSetting.slug == "mock_provider").first()
    if not setting:
        setting = ProviderSetting(
            name="Mock Provider Test",
            slug="mock_provider",
            api_url="https://api.mockprovider.com/v2",
            api_key="secret-mock-key-12345",
            timeout_seconds=15,
            max_retries=2,
            rate_limit_rpm=120,
            is_active=True,
            is_default=False,
        )
        db.add(setting)
        db.commit()
    db.close()
    yield
    # Cleanup


# ---------------------------------------------------------------------------
# 1. Adapter and Registry Tests
# ---------------------------------------------------------------------------

def test_registry_registration_and_lookup():
    assert "mock_provider" in list_registered_slugs()
    adapter_cls = get_adapter_class("mock_provider")
    assert adapter_cls is MockSmmAdapter


def test_build_adapter():
    db = SessionLocal()
    setting = db.query(ProviderSetting).filter(ProviderSetting.slug == "mock_provider").first()
    assert setting is not None
    adapter = build_adapter(setting)
    assert isinstance(adapter, MockSmmAdapter)
    assert adapter.slug == "mock_provider"
    assert adapter.config.api_key == "secret-mock-key-12345"
    db.close()


def test_adapter_operations():
    config = ProviderConfig(slug="mock_provider", api_url="https://mock.com", api_key="abc")
    adapter = MockSmmAdapter(config)

    # Balance
    balance = adapter.get_balance()
    assert balance == Decimal("150.75")

    # Services
    services = adapter.get_services()
    assert len(services) == 2
    assert services[0].provider_service_id == "101"

    # Order
    order_res = adapter.add_order("101", "https://instagram.com/p/123", 100)
    assert order_res.provider_order_id == "99901"

    # Status
    status = adapter.get_order_status("99901")
    assert status.status == "In progress"

    # Multi status
    multi = adapter.get_multiple_order_statuses(["99901", "99902"])
    assert len(multi) == 2
    assert multi[0].status == "Completed"

    # Refill & Cancel
    assert adapter.refill_order("99901") == "refill_888"
    assert adapter.get_refill_status("refill_888") == "Completed"
    assert "99901" in adapter.cancel_orders(["99901"])


# ---------------------------------------------------------------------------
# 2. Service Sync & Deduplication Tests
# ---------------------------------------------------------------------------

def test_import_services_deduplication():
    db = SessionLocal()
    setting = db.query(ProviderSetting).filter(ProviderSetting.slug == "mock_provider").first()

    # Import once
    res1 = import_services_from_provider(db, setting)
    assert res1["provider"] == "mock_provider"
    assert res1["imported"] + res1["updated"] >= 2

    count_after_first = db.query(Service).filter(Service.provider_name == "mock_provider").count()

    # Second import - must update existing records, never duplicate
    res2 = import_services_from_provider(db, setting)
    assert res2["imported"] == 0  # Deduplication: exactly 0 new records created
    assert res2["updated"] >= 2   # Updated existing records

    count_after_second = db.query(Service).filter(Service.provider_name == "mock_provider").count()
    assert count_after_first == count_after_second  # Count is unchanged (no duplicates)

    db.close()


# ---------------------------------------------------------------------------
# 3. Admin Providers API Tests
# ---------------------------------------------------------------------------

def test_admin_list_providers(client, admin_headers):
    res = client.get("/admin/providers", headers=admin_headers)
    assert res.status_code == 200
    data = res.json()
    assert data["success"] is True
    assert "mock_provider" in data["registered_slugs"]
    slugs = [p["slug"] for p in data["providers"]]
    assert "mock_provider" in slugs


def test_admin_get_provider_balance(client, admin_headers):
    db = SessionLocal()
    setting = db.query(ProviderSetting).filter(ProviderSetting.slug == "mock_provider").first()
    pid = setting.id
    db.close()

    res = client.get(f"/admin/providers/{pid}/balance", headers=admin_headers)
    assert res.status_code == 200
    data = res.json()
    assert data["success"] is True
    assert data["provider"] == "mock_provider"
    assert data["balance"] == "150.75"


def test_admin_sync_provider_services(client, admin_headers):
    db = SessionLocal()
    setting = db.query(ProviderSetting).filter(ProviderSetting.slug == "mock_provider").first()
    pid = setting.id
    db.close()

    res = client.post(f"/admin/providers/{pid}/sync-services", headers=admin_headers)
    assert res.status_code == 200
    data = res.json()
    assert data["success"] is True
    assert data["provider"] == "mock_provider"


def test_admin_remapping_service(client, admin_headers):
    db = SessionLocal()
    service = db.query(Service).filter(Service.provider_name == "mock_provider").first()
    assert service is not None
    service_id = service.id
    db.close()

    # Re-map service to different provider_service_id
    res = client.patch(
        f"/admin/providers/services/{service_id}/mapping",
        headers=admin_headers,
        json={"provider_slug": "mock_provider", "provider_service_id": 999},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["success"] is True
    assert data["new_provider_service_id"] == 999

    # Verify in DB
    db = SessionLocal()
    updated_svc = db.get(Service, service_id)
    assert updated_svc.provider_service_id == 999
    # Restore back to 101
    updated_svc.provider_service_id = 101
    db.commit()
    db.close()


def test_admin_delete_provider_with_services_fails(client, admin_headers):
    db = SessionLocal()
    setting = db.query(ProviderSetting).filter(ProviderSetting.slug == "mock_provider").first()
    pid = setting.id
    db.close()

    # Deleting provider that still has mapped services should return 409
    res = client.delete(f"/admin/providers/{pid}", headers=admin_headers)
    assert res.status_code == 409
    assert "still mapped" in res.json()["detail"]


# ---------------------------------------------------------------------------
# 4. Order Placement & Immutable Snapshotting Tests
# ---------------------------------------------------------------------------

def test_order_creation_with_provider_snapshot(client, regular_user, user_headers):
    db = SessionLocal()
    user = db.get(User, regular_user.id)
    user.balance = Decimal("500.00")
    db.commit()

    service = db.query(Service).filter(Service.provider_name == "mock_provider").first()
    service.is_active = True
    service.customer_visible = True
    service.provider_available = True
    service.selling_rate = Decimal("10.00")
    service.min_quantity = 100
    service.max_quantity = 1000
    db.commit()
    service_id = service.id
    db.close()

    res = client.post(
        "/orders",
        headers=user_headers,
        json={"service_id": service_id, "link": "https://instagram.com/test", "quantity": 100},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["success"] is True
    order_id = data["order_id"]

    # Verify immutable snapshot in database
    db = SessionLocal()
    order = db.get(Order, order_id)
    assert order is not None
    assert order.snapshot_provider_slug == "mock_provider"
    assert order.snapshot_provider_service_id == str(service.provider_service_id)
    assert order.provider_order_id == 99901

    # Now verify multi-provider order status sync
    sync_res = sync_order_statuses(db)
    assert sync_res["checked"] >= 1
    db.refresh(order)
    assert order.status == "Completed"

    db.close()


def test_order_failure_auto_refund_without_cross_provider_fallback(client, regular_user, user_headers):
    """
    Requirement 11: Never submit to multiple providers automatically after failure/timeout.
    Verify provider error causes atomic refund, never silent cross-provider retry.
    """
    db = SessionLocal()
    user = db.get(User, regular_user.id)
    user.balance = Decimal("200.00")
    db.commit()
    initial_balance = user.balance

    # Create a service mapped to failure
    fail_svc = Service(
        provider_service_id=99999,
        name="Failing Service",
        category="Test",
        service_type="Default",
        provider_rate=Decimal("1.00"),
        selling_rate=Decimal("2.00"),
        min_quantity=10,
        max_quantity=1000,
        provider_name="mock_provider",
        provider_available=True,
        customer_visible=True,
        is_active=True,
    )
    db.add(fail_svc)
    db.commit()
    db.refresh(fail_svc)
    fail_svc_id = fail_svc.id
    db.close()

    # We patch add_order on MockSmmAdapter to simulate a timeout or ProviderError
    with patch.object(MockSmmAdapter, "add_order", side_effect=ProviderError("Provider timeout")):
        res = client.post(
            "/orders",
            headers=user_headers,
            json={"service_id": fail_svc_id, "link": "https://test.com/fail", "quantity": 10},
        )
        assert res.status_code == 400
        assert "Provider timeout" in res.json()["detail"]

    # Verify user wallet was refunded
    db = SessionLocal()
    user_after = db.get(User, regular_user.id)
    assert user_after.balance == initial_balance

    # Verify no stray order record was created
    orders_created = db.query(Order).filter(Order.service_id == fail_svc_id).all()
    assert len(orders_created) == 0

    # Cleanup temporary test service
    db.query(Service).filter(Service.id == fail_svc_id).delete()
    db.commit()

    db.close()


# ---------------------------------------------------------------------------
# 5. SMMXpert Provider Adapter Tests
# ---------------------------------------------------------------------------

def test_smmxpert_adapter_all_actions():
    """Verify SmmXpertAdapter correctly parses responses from smmxpert.com API."""
    from app.providers.smmxpert import SmmXpertAdapter

    config = ProviderConfig(
        slug="smmxpert",
        api_url="https://smmxpert.com/api/v2",
        api_key="23c9c-test-key-73c3d",
    )
    adapter = SmmXpertAdapter(config)

    # 1. Services
    with patch.object(adapter, "_post", return_value=[
        {
            "service": 1,
            "name": "Followers",
            "type": "Default",
            "category": "First Category",
            "rate": "0.90",
            "min": "50",
            "max": "10000",
        },
        {
            "service": 2,
            "name": "Comments",
            "type": "Custom Comments",
            "category": "Second Category",
            "rate": "8",
            "min": "10",
            "max": "1500",
        },
    ]):
        services = adapter.get_services()
        assert len(services) == 2
        assert services[0].provider_service_id == "1"
        assert services[0].name == "Followers"
        assert services[0].rate == Decimal("0.90")
        assert services[1].rate == Decimal("8")

    # 2. Balance
    with patch.object(adapter, "_post", return_value={"balance": 10000, "currency": "USD"}):
        balance = adapter.get_balance()
        assert balance == Decimal("10000")

    # 3. Add order (checks 'url' parameter)
    captured_payload = {}
    def mock_post(payload):
        nonlocal captured_payload
        captured_payload = payload
        return {"order": 23501}

    with patch.object(adapter, "_post", side_effect=mock_post):
        result = adapter.add_order(provider_service_id="1", link="https://instagram.com/p/abc", quantity=100)
        assert result.provider_order_id == "23501"
        assert captured_payload["action"] == "add"
        assert captured_payload["url"] == "https://instagram.com/p/abc"
        assert captured_payload["quantity"] == 100

    # 4. Status single
    with patch.object(adapter, "_post", return_value={
        "charge": "0.27819",
        "start_count": "3572",
        "status": "Partial",
        "remains": "157",
        "currency": "USD",
    }):
        status = adapter.get_order_status("23501")
        assert status.status == "Partial"
        assert status.start_count == 3572
        assert status.remains == 157
        assert status.charge == Decimal("0.27819")

    # 5. Status multiple
    with patch.object(adapter, "_post", return_value={
        "1": {"charge": "0.27819", "start_count": "3572", "status": "Partial", "remains": "157", "currency": "USD"},
        "10": {"error": "Incorrect order ID"},
        "100": {"charge": "1.44219", "start_count": "234", "status": "In progress", "remains": "10", "currency": "USD"},
    }):
        multi = adapter.get_multiple_order_statuses(["1", "10", "100"])
        assert len(multi) == 3
        assert multi[0].status == "Partial"
        assert multi[1].error == "Incorrect order ID"
        assert multi[2].status == "In progress"

    # 6. Refill
    with patch.object(adapter, "_post", return_value={
        "status": "Success",
        "message": "Your order successfully added for refill. Thank you for patience.",
    }):
        refill_id = adapter.refill_order("23501")
        assert refill_id == "23501"

    # 7. Cancel
    with patch.object(adapter, "_post", return_value={
        "status": "Success",
        "message": "Your order was successfully marked for cancellation. Thank you for patience.",
    }):
        cancel_res = adapter.cancel_orders(["23501"])
        assert "23501" in cancel_res
        assert cancel_res["23501"]["status"] == "Success"

