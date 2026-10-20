"""
SMMXpert provider adapter.
Implements SmmProviderAdapter for SMMXpert API (https://smmxpert.com/api/v2).
Handles SMMXpert-specific protocols such as 'url' parameter for orders,
single 'order' parameter for cancellation, and message-based refill responses.
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


class SmmXpertAdapter(SmmProviderAdapter):
    """
    Adapter for SMMXpert (https://smmxpert.com/api/v2).
    """

    @classmethod
    def create_from_env(cls) -> SmmXpertAdapter:
        """Create adapter using SMMXPERT_API_URL and SMMXPERT_API_KEY env vars."""
        api_url = os.getenv("SMMXPERT_API_URL", "https://smmxpert.com/api/v2")
        api_key = os.getenv("SMMXPERT_API_KEY", "")
        if not api_key:
            raise ValueError("SMMXPERT_API_KEY is not configured")
        return cls(ProviderConfig(slug="smmxpert", api_url=api_url, api_key=api_key))

    def _post(self, data: dict[str, Any]) -> Any:
        """POST to SMMXpert API with exponential backoff on transient network errors."""
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
                raise ProviderError(
                    f"SMMXpert HTTP {err.response.status_code}: {err}"
                ) from err
            except httpx.HTTPError as err:
                last_error = err
                if attempt < self.config.max_retries - 1:
                    time.sleep(2 ** attempt)

        raise ProviderError(
            f"SMMXpert request failed after {self.config.max_retries} attempts: {last_error}"
        )

    # ------------------------------------------------------------------
    # SmmProviderAdapter Implementation
    # ------------------------------------------------------------------

    def get_services(self) -> list[ProviderService]:
        raw = self._post({"action": "services"})
        if not isinstance(raw, list):
            raise ProviderError("Unexpected services response format from SMMXpert", {})

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
                        refill=bool(item.get("refill", True)),
                        cancel=bool(item.get("cancel", True)),
                    )
                )
            except (KeyError, ValueError):
                continue
        return services

    def get_balance(self) -> Decimal:
        raw = self._post({"action": "balance"})
        if not isinstance(raw, dict) or "balance" not in raw:
            raise ProviderError(
                "Unexpected balance response from SMMXpert",
                raw if isinstance(raw, dict) else {},
            )
        return Decimal(str(raw["balance"]))

    def add_order(
        self,
        provider_service_id: str,
        link: str,
        quantity: int,
    ) -> ProviderOrderResult:
        """
        SMMXpert specifies 'url' as the link parameter.
        We send both 'url' and 'link' to ensure complete compatibility.
        """
        raw = self._post({
            "action": "add",
            "service": provider_service_id,
            "url": link.strip(),
            "link": link.strip(),
            "quantity": quantity,
        })
        if not isinstance(raw, dict):
            raise ProviderError("Invalid order response from SMMXpert", {})
        if "error" in raw:
            raise ProviderError(str(raw["error"]), raw)

        order_id = raw.get("order")
        if not order_id:
            raise ProviderError("No order ID in SMMXpert response", raw)
        return ProviderOrderResult(provider_order_id=str(order_id), raw_response=raw)

    def get_order_status(self, provider_order_id: str) -> ProviderOrderStatus:
        raw = self._post({"action": "status", "order": provider_order_id})
        if not isinstance(raw, dict):
            return ProviderOrderStatus(
                provider_order_id=provider_order_id,
                status="Unknown",
                error="Non-dict response from SMMXpert",
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
        """
        SMMXpert refill endpoint returns:
        {"status": "Success", "message": "Your order successfully added for refill..."}
        We use the provider_order_id as the tracking identifier.
        """
        raw = self._post({"action": "refill", "order": provider_order_id})
        if not isinstance(raw, dict):
            raise ProviderError("Invalid refill response from SMMXpert", {})
        if "error" in raw:
            raise ProviderError(str(raw["error"]), raw)

        # Check for success
        status = str(raw.get("status", "")).lower()
        if "success" in status or raw.get("refill") or "successfully" in str(raw.get("message", "")).lower():
            # Return either explicit refill ID or provider_order_id
            refill_id = raw.get("refill") or provider_order_id
            return str(refill_id)

        raise ProviderError(raw.get("message", "SMMXpert rejected refill request"), raw)

    def get_refill_status(self, refill_id: str) -> str:
        """
        Queries refill status or checks order status.
        """
        try:
            raw = self._post({"action": "refill_status", "refill": refill_id})
            if isinstance(raw, dict) and "status" in raw:
                return str(raw["status"])
        except Exception:
            pass

        # If provider doesn't support separate refill_status action, query order status
        try:
            order_status = self.get_order_status(refill_id)
            return order_status.status
        except Exception:
            return "Pending"

    def cancel_orders(self, provider_order_ids: list[str]) -> dict[str, Any]:
        """
        SMMXpert uses 'order' (singular). We handle each order ID.
        """
        results: dict[str, Any] = {}
        for oid in provider_order_ids:
            try:
                res = self._post({"action": "cancel", "order": oid})
                results[str(oid)] = res
            except Exception as e:
                results[str(oid)] = {"error": str(e)}
        return results

    @staticmethod
    def _parse_status(pid: str, data: dict) -> ProviderOrderStatus:
        charge_val = data.get("charge")
        return ProviderOrderStatus(
            provider_order_id=pid,
            status=str(data.get("status", "Unknown")),
            start_count=(
                int(data["start_count"]) if data.get("start_count") is not None and str(data["start_count"]).isdigit() else None
            ),
            remains=(
                int(data["remains"]) if data.get("remains") is not None and str(data["remains"]).isdigit() else None
            ),
            charge=Decimal(str(charge_val)) if charge_val is not None else None,
            error=str(data["error"]) if data.get("error") else None,
        )
