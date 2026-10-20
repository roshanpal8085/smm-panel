"""
VipSmmPro provider adapter.
Implements SmmProviderAdapter for the standard SMM panel v2 API protocol
(compatible with vipsmmpro.com and any panel using the same endpoint spec).
"""
from __future__ import annotations

import os
import time
from decimal import Decimal
from typing import Any

import httpx
from dotenv import load_dotenv

from app.providers.base import (
    ProviderConfig,
    ProviderError,
    ProviderOrderResult,
    ProviderOrderStatus,
    ProviderService,
    SmmProviderAdapter,
)

load_dotenv()


class VipSmmProAdapter(SmmProviderAdapter):
    """
    Adapter for the standard SMM panel v2 API protocol.
    Compatible with vipsmmpro.com and many other providers.
    """

    @classmethod
    def create_from_env(cls) -> VipSmmProAdapter:
        """Convenience factory using legacy PROVIDER_API_URL / PROVIDER_API_KEY env vars."""
        api_url = os.getenv("PROVIDER_API_URL")
        api_key = os.getenv("PROVIDER_API_KEY")
        if not api_url:
            raise ValueError("PROVIDER_API_URL is not configured")
        if not api_key:
            raise ValueError("PROVIDER_API_KEY is not configured")
        return cls(ProviderConfig(slug="vipsmmpro", api_url=api_url, api_key=api_key))

    # ------------------------------------------------------------------
    # Internal HTTP helper
    # ------------------------------------------------------------------

    def _post(self, data: dict[str, Any]) -> Any:
        """POST to the provider API with exponential-backoff retry on transient errors."""
        payload = {"key": self.config.api_key, **data}
        last_error: Exception | None = None

        for attempt in range(max(1, self.config.max_retries)):
            try:
                response = httpx.post(
                    self.config.api_url,
                    data=payload,
                    timeout=self.config.timeout,
                )
                response.raise_for_status()
                return response.json()
            except httpx.HTTPStatusError as err:
                # 4xx / 5xx — no point retrying
                raise ProviderError(
                    f"Provider HTTP {err.response.status_code}: {err}"
                ) from err
            except httpx.HTTPError as err:
                last_error = err
                if attempt < self.config.max_retries - 1:
                    time.sleep(2 ** attempt)  # exponential back-off: 1s, 2s, 4s

        raise ProviderError(
            f"Provider request failed after {self.config.max_retries} attempts: {last_error}"
        )

    # ------------------------------------------------------------------
    # SmmProviderAdapter interface
    # ------------------------------------------------------------------

    def get_services(self) -> list[ProviderService]:
        raw = self._post({"action": "services"})
        if not isinstance(raw, list):
            raise ProviderError("Unexpected services response format", {})
        services: list[ProviderService] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            try:
                services.append(
                    ProviderService(
                        provider_service_id=str(item["service"]),
                        name=str(item.get("name", "")),
                        category=str(item.get("category", "Uncategorized")),
                        service_type=str(item.get("type", "Default")),
                        rate=Decimal(str(item.get("rate", "0"))),
                        min_quantity=int(item.get("min", 0)),
                        max_quantity=int(item.get("max", 0)),
                        refill=bool(item.get("refill", False)),
                        cancel=bool(item.get("cancel", False)),
                    )
                )
            except (KeyError, ValueError):
                continue
        return services

    def get_balance(self) -> Decimal:
        raw = self._post({"action": "balance"})
        if not isinstance(raw, dict) or "balance" not in raw:
            raise ProviderError(
                "Unexpected balance response",
                raw if isinstance(raw, dict) else {},
            )
        return Decimal(str(raw["balance"]))

    def add_order(
        self,
        provider_service_id: str,
        link: str,
        quantity: int,
    ) -> ProviderOrderResult:
        raw = self._post({
            "action": "add",
            "service": provider_service_id,
            "link": link,
            "quantity": quantity,
        })
        if not isinstance(raw, dict):
            raise ProviderError("Invalid add-order response", {})
        if "error" in raw:
            raise ProviderError(str(raw["error"]), raw)
        order_id = raw.get("order")
        if not order_id:
            raise ProviderError("No order ID in provider response", raw)
        return ProviderOrderResult(provider_order_id=str(order_id), raw_response=raw)

    def get_order_status(self, provider_order_id: str) -> ProviderOrderStatus:
        raw = self._post({"action": "status", "order": provider_order_id})
        if not isinstance(raw, dict):
            return ProviderOrderStatus(
                provider_order_id=provider_order_id,
                status="Unknown",
                error="Non-dict response from provider",
            )
        return self._parse_status(provider_order_id, raw)

    def get_multiple_order_statuses(
        self, provider_order_ids: list[str]
    ) -> list[ProviderOrderStatus]:
        if not provider_order_ids:
            return []
        raw = self._post({
            "action": "status",
            "orders": ",".join(provider_order_ids),
        })
        if not isinstance(raw, dict):
            return [
                ProviderOrderStatus(pid, "Unknown", error="Non-dict bulk response")
                for pid in provider_order_ids
            ]
        results: list[ProviderOrderStatus] = []
        for pid in provider_order_ids:
            data = raw.get(str(pid)) or raw.get(pid)
            if data is None:
                results.append(ProviderOrderStatus(pid, "Unknown", error="Not in response"))
            elif isinstance(data, dict):
                results.append(self._parse_status(str(pid), data))
            else:
                results.append(ProviderOrderStatus(str(pid), "Unknown", error=str(data)))
        return results

    def refill_order(self, provider_order_id: str) -> str:
        raw = self._post({"action": "refill", "order": provider_order_id})
        if not isinstance(raw, dict):
            raise ProviderError("Invalid refill response", {})
        if "error" in raw:
            raise ProviderError(str(raw["error"]), raw)
        refill_id = raw.get("refill")
        if not refill_id:
            raise ProviderError("No refill ID in provider response", raw)
        return str(refill_id)

    def get_refill_status(self, refill_id: str) -> str:
        raw = self._post({"action": "refill_status", "refill": refill_id})
        if not isinstance(raw, dict):
            raise ProviderError("Invalid refill status response", {})
        return str(raw.get("status", "Unknown"))

    def cancel_orders(self, provider_order_ids: list[str]) -> dict[str, Any]:
        raw = self._post({
            "action": "cancel",
            "orders": ",".join(provider_order_ids),
        })
        return raw if isinstance(raw, dict) else {}

    # ------------------------------------------------------------------
    # Helper
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_status(pid: str, data: dict) -> ProviderOrderStatus:
        charge_val = data.get("charge")
        return ProviderOrderStatus(
            provider_order_id=pid,
            status=str(data.get("status", "Unknown")),
            start_count=(
                int(data["start_count"]) if data.get("start_count") is not None else None
            ),
            remains=int(data["remains"]) if data.get("remains") is not None else None,
            charge=Decimal(str(charge_val)) if charge_val is not None else None,
            error=str(data["error"]) if data.get("error") else None,
        )


# ---------------------------------------------------------------------------
# Backwards-compatibility shim — keeps all existing callers working unchanged
# ---------------------------------------------------------------------------

class VipSmmProClient:
    """
    Legacy shim retained for backwards compatibility.
    All existing imports of VipSmmProClient continue to work.
    New code should use VipSmmProAdapter via the provider registry.
    """

    def __init__(self) -> None:
        self._adapter = VipSmmProAdapter.create_from_env()

    def get_services(self):
        return self._adapter._post({"action": "services"})

    def get_balance(self):
        return self._adapter._post({"action": "balance"})

    def add_order(self, service: int, link: str, quantity: int):
        return self._adapter._post(
            {"action": "add", "service": service, "link": link, "quantity": quantity}
        )

    def get_order_status(self, order_id: int):
        return self._adapter._post({"action": "status", "order": order_id})

    def get_multiple_order_status(self, order_ids: list[int]):
        return self._adapter._post(
            {"action": "status", "orders": ",".join(str(i) for i in order_ids)}
        )

    def refill_order(self, order_id: int):
        return self._adapter._post({"action": "refill", "order": order_id})

    def get_refill_status(self, refill_id: int):
        return self._adapter._post({"action": "refill_status", "refill": refill_id})

    def cancel_orders(self, order_ids: list[int]):
        return self._adapter._post(
            {"action": "cancel", "orders": ",".join(str(i) for i in order_ids)}
        )