# 🔌 Adding a New SMM Provider to SMM PRIME

This guide explains how to add, configure, and manage new upstream SMM providers in the SMM PRIME multi-provider engine.

---

## Architecture Overview

The system uses an **Adapter Pattern** with an immutable snapshot model:

```
                  ┌──────────────────────────────┐
                  │   SmmProviderAdapter (ABC)   │
                  │   app/providers/base.py      │
                  └──────────────┬───────────────┘
                                 │
         ┌───────────────────────┴───────────────────────┐
         ▼                                               ▼
┌──────────────────────────────┐         ┌──────────────────────────────┐
│       VipSmmProAdapter       │         │      CustomProviderAdapter   │
│   (Standard SMM v2 API)      │         │     (Custom API Protocol)    │
└──────────────────────────────┘         └──────────────────────────────┘
                                 │
                  ┌──────────────┴───────────────┐
                  ▼                              ▼
     Provider Registry               Admin Provider API
   app/providers/registry.py        app/routers/providers.py
                  │                              │
                  └──────────────┬───────────────┘
                                 ▼
                    Multi-Provider Service Sync
                    app/services/provider_sync.py
```

### Safety & Integrity Guarantees

1. **Immutable Order Snapshots**:
   Every order placed stores an immutable snapshot of `snapshot_provider_slug` and `snapshot_provider_service_id`. Future changes to service mapping or provider credentials will **never** alter historical order routing or tracking.
2. **Deduplication Key**:
   Services are imported without duplicates using the composite key `(provider_name, provider_service_id)`. Re-syncing updates mutable catalogue fields (pricing, limits, description) and marks delisted services as unavailable without duplicating rows.
3. **Never Cross-Provider Retry (Requirement 11)**:
   If an order times out or fails at provider A, the system does **not** silently submit the same order to provider B. It atomically refunds the user's wallet with an audit trail to avoid duplicate fulfillment.

---

## Step 1: If Provider Uses Standard SMM API (v2)

Most SMM panels (SmartPanel, RentalPanel, PerfectPanel, etc.) share the standard SMM v2 protocol:
- `action=services`
- `action=add`
- `action=status` / `action=status&orders=...`
- `action=refill` / `action=refill_status`
- `action=cancel`
- `action=balance`

If your new provider uses this standard API:

1. Open `app/providers/registry.py`
2. Register the provider slug with `VipSmmProAdapter`:

```python
from app.providers.vipsmmpro import VipSmmProAdapter
from app.providers.registry import register_provider

# Example: registering a new provider with slug 'smmstone'
register_provider("smmstone", VipSmmProAdapter)
```

---

## Step 2: If Provider Uses a Custom API Protocol

If your provider uses a different protocol, implement a custom adapter:

1. Create `app/providers/my_provider.py`:

```python
from decimal import Decimal
from typing import Any
import httpx

from app.providers.base import (
    ProviderConfig,
    ProviderError,
    ProviderOrderResult,
    ProviderOrderStatus,
    ProviderService,
    SmmProviderAdapter,
)

class MyCustomProviderAdapter(SmmProviderAdapter):
    """Adapter for My Custom Provider API."""

    def get_services(self) -> list[ProviderService]:
        # Call provider API and normalize into ProviderService objects
        response = httpx.get(
            f"{self.config.api_url}/catalog",
            headers={"Authorization": f"Bearer {self.config.api_key}"},
            timeout=self.config.timeout,
        )
        data = response.json()
        return [
            ProviderService(
                provider_service_id=str(item["id"]),
                name=item["title"],
                category=item["category"],
                service_type="Default",
                rate=Decimal(str(item["price_per_k"])),
                min_quantity=int(item["min"]),
                max_quantity=int(item["max"]),
                refill=bool(item.get("refill_supported")),
                cancel=bool(item.get("cancel_supported")),
            )
            for item in data["services"]
        ]

    def get_balance(self) -> Decimal:
        # Return provider account balance
        response = httpx.get(...)
        return Decimal(str(response.json()["balance"]))

    def add_order(self, provider_service_id: str, link: str, quantity: int) -> ProviderOrderResult:
        # Submit order and return ProviderOrderResult with string provider_order_id
        response = httpx.post(...)
        data = response.json()
        if "error" in data:
            raise ProviderError(data["error"])
        return ProviderOrderResult(provider_order_id=str(data["order_id"]), raw_response=data)

    def get_order_status(self, provider_order_id: str) -> ProviderOrderStatus:
        # Return ProviderOrderStatus
        response = httpx.get(...)
        data = response.json()
        return ProviderOrderStatus(
            provider_order_id=provider_order_id,
            status=data["status"],
            start_count=data.get("start_count"),
            remains=data.get("remains"),
            charge=Decimal(str(data["charge"])) if data.get("charge") else None,
        )

    def refill_order(self, provider_order_id: str) -> str:
        # Request refill and return refill ID
        ...

    def get_refill_status(self, refill_id: str) -> str:
        # Return refill status string
        ...

    def cancel_orders(self, provider_order_ids: list[str]) -> dict[str, Any]:
        # Cancel orders and return response
        ...
```

2. Register your custom adapter in `app/providers/registry.py`:

```python
from app.providers.my_provider import MyCustomProviderAdapter

register_provider("my_provider", MyCustomProviderAdapter)
```

---

## Step 3: Configure Provider in Admin API

Once registered in the code, register the credentials in the database via the Admin API:

### 1. Register Provider
```http
POST /admin/providers
Authorization: Bearer <ADMIN_JWT_TOKEN>
Content-Type: application/json

{
  "name": "SMM Stone Global",
  "slug": "smmstone",
  "api_url": "https://smmstone.com/api/v2",
  "api_key": "your-secret-api-key-here",
  "timeout_seconds": 30,
  "max_retries": 3,
  "rate_limit_rpm": 120,
  "is_active": true,
  "is_default": false
}
```

### 2. Check Live Provider Balance
```http
GET /admin/providers/{provider_id}/balance
Authorization: Bearer <ADMIN_JWT_TOKEN>
```

### 3. Import Services
```http
POST /admin/providers/{provider_id}/sync-services
Authorization: Bearer <ADMIN_JWT_TOKEN>
```
Or sync all active providers:
```http
POST /admin/providers/sync-all
Authorization: Bearer <ADMIN_JWT_TOKEN>
```

### 4. Re-map an Internal Service (Optional)
To switch an internal service to route future orders to a different provider:
```http
PATCH /admin/providers/services/{service_id}/mapping
Authorization: Bearer <ADMIN_JWT_TOKEN>
Content-Type: application/json

{
  "provider_slug": "smmstone",
  "provider_service_id": 412
}
```

---

## Step 4: Verification & Testing

Run the test suite with mocked providers:

```powershell
.venv\Scripts\pytest.exe tests/test_multi_provider.py -v
```
All tests should pass.
