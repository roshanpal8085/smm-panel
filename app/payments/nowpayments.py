"""
NOWPayments USDT Payment Provider
----------------------------------
Production-grade integration with NOWPayments API.
- Creates per-invoice payment requests with unique order IDs
- Verifies IPN webhooks with HMAC-SHA512 using constant-time comparison
- Supports USDT on TRC20 / BSC / ERC20 / Polygon / Solana
- Credits wallet in USDT (NOT INR) — separate balance column
- Never trusts frontend; always verifies server-side via GET /payment/{id}
"""

import hashlib
import hmac
import json
import logging
import os
import time
from decimal import Decimal
from typing import Any, Dict, List, Optional

import httpx

from app.payments.base import (
    BasePaymentProvider,
    PaymentOrderResult,
    PaymentVerificationResult,
    WebhookResult,
)

logger = logging.getLogger(__name__)

# -------------------------------------------------------------------------
# NOWPayments payment statuses (from official docs)
# -------------------------------------------------------------------------
# Intermediate statuses — NOT yet safe to credit
NOWPAYMENTS_PENDING_STATUSES = {
    "waiting",          # Waiting for customer to send funds
    "confirming",       # Transaction detected, waiting for confirmations
    "sending",          # Funds being sent to merchant payout address
}

# Safe to credit — payment fully confirmed
NOWPAYMENTS_SUCCESS_STATUSES = {
    "finished",         # Payment confirmed and complete
    "partially_paid",   # Underpayment received (handled by config — credit what arrived)
}

# Terminal failure statuses — do NOT credit
NOWPAYMENTS_FAILED_STATUSES = {
    "failed",
    "refunded",
    "expired",
}

# Supported USDT networks with display info
USDT_NETWORKS: List[Dict[str, Any]] = [
    {
        "id":           "usdtbsc",
        "label":        "USDT (BEP-20 / BSC)",
        "network":      "bsc",
        "min_usdt":     Decimal("1.00"),
        "icon":         "🟡",
        "description":  "Binance Smart Chain — low fees, fast confirmation",
    },
    {
        "id":           "usdttrc20",
        "label":        "USDT (TRC-20 / TRON)",
        "network":      "tron",
        "min_usdt":     Decimal("11.00"),
        "icon":         "🔴",
        "description":  "TRON network — widely supported, higher minimum",
    },
    {
        "id":           "usdtmatic",
        "label":        "USDT (Polygon)",
        "network":      "matic",
        "min_usdt":     Decimal("1.00"),
        "icon":         "🟣",
        "description":  "Polygon / MATIC — very low fees",
    },
    {
        "id":           "usdterc20",
        "label":        "USDT (ERC-20 / Ethereum)",
        "network":      "eth",
        "min_usdt":     Decimal("1.00"),
        "icon":         "🔵",
        "description":  "Ethereum network — highest fees, most recognized",
    },
    {
        "id":           "usdtsol",
        "label":        "USDT (Solana SPL)",
        "network":      "sol",
        "min_usdt":     Decimal("2.00"),
        "icon":         "🟢",
        "description":  "Solana network — very fast, low fees",
    },
]

# Map currency_id → min_usdt for quick lookup
_NETWORK_MIN = {n["id"]: n["min_usdt"] for n in USDT_NETWORKS}


class NOWPaymentsProvider(BasePaymentProvider):
    """
    NOWPayments USDT payment provider.

    Environment variables required:
        NOWPAYMENTS_API_KEY     — API key from NOWPayments dashboard
        NOWPAYMENTS_IPN_SECRET  — IPN secret set in NOWPayments dashboard
    """

    name = "nowpayments"
    BASE_URL = "https://api.nowpayments.io/v1"
    TIMEOUT = 25.0       # seconds per request
    MAX_RETRIES = 2      # retries on 5xx / timeout

    def __init__(self):
        self.api_key = os.getenv("NOWPAYMENTS_API_KEY", "").strip()
        self.ipn_secret = os.getenv("NOWPAYMENTS_IPN_SECRET", "").strip()

    def is_configured(self) -> bool:
        return bool(self.api_key and self.ipn_secret)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _headers(self) -> Dict[str, str]:
        return {
            "x-api-key": self.api_key,
            "Content-Type": "application/json",
        }

    def _api_get(self, path: str, params: Optional[Dict] = None) -> Dict:
        """Performs an authenticated GET with retry logic."""
        url = f"{self.BASE_URL}{path}"
        last_exc = None
        for attempt in range(self.MAX_RETRIES + 1):
            try:
                with httpx.Client(timeout=self.TIMEOUT) as client:
                    r = client.get(url, headers=self._headers(), params=params)
                    r.raise_for_status()
                    return r.json()
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code < 500:
                    raise
                last_exc = exc
            except httpx.RequestError as exc:
                last_exc = exc
            if attempt < self.MAX_RETRIES:
                time.sleep(1.5 * (attempt + 1))
        raise last_exc or RuntimeError(f"GET {path} failed after retries")

    def _api_post(self, path: str, payload: Dict) -> Dict:
        """Performs an authenticated POST with retry logic."""
        url = f"{self.BASE_URL}{path}"
        last_exc = None
        for attempt in range(self.MAX_RETRIES + 1):
            try:
                with httpx.Client(timeout=self.TIMEOUT) as client:
                    r = client.post(url, headers=self._headers(), json=payload)
                    r.raise_for_status()
                    return r.json()
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code < 500:
                    raise
                last_exc = exc
            except httpx.RequestError as exc:
                last_exc = exc
            if attempt < self.MAX_RETRIES:
                time.sleep(1.5 * (attempt + 1))
        raise last_exc or RuntimeError(f"POST {path} failed after retries")

    # ------------------------------------------------------------------
    # Public provider interface
    # ------------------------------------------------------------------

    def create_order(
        self,
        amount: Decimal,           # USDT amount requested
        currency: str,             # Should be "USDT"
        deposit_reference: str,    # Unique internal reference (becomes order_id)
        user_id: int,
        webhook_url: Optional[str] = None,
        redirect_url: Optional[str] = None,
        pay_currency: str = "usdtbsc",  # NOWPayments currency code
        **kwargs,
    ) -> PaymentOrderResult:
        if not self.is_configured():
            return PaymentOrderResult(
                success=False,
                order_id="",
                amount=amount,
                currency=currency,
                error_message=(
                    "NOWPayments credentials not configured. "
                    "Set NOWPAYMENTS_API_KEY and NOWPAYMENTS_IPN_SECRET in environment."
                ),
            )

        # Validate minimum for the chosen network
        min_amt = _NETWORK_MIN.get(pay_currency.lower(), Decimal("1.00"))
        if amount < min_amt:
            return PaymentOrderResult(
                success=False,
                order_id="",
                amount=amount,
                currency=currency,
                error_message=(
                    f"Minimum deposit for {pay_currency.upper()} is "
                    f"{min_amt} USDT. Please enter a higher amount."
                ),
            )

        payload: Dict[str, Any] = {
            "price_amount":    float(amount),
            "price_currency":  "usd",       # price in USD = USDT amount
            "pay_currency":    pay_currency.lower(),
            "order_id":        deposit_reference,
            "order_description": f"Yoomo Wallet Deposit — User #{user_id}",
            "is_fixed_rate":   False,
            "is_fee_paid_by_user": False,
        }
        if webhook_url:
            payload["ipn_callback_url"] = webhook_url
        if redirect_url:
            payload["success_url"] = redirect_url

        try:
            data = self._api_post("/payment", payload)
            pay_address = data.get("pay_address", "")
            pay_amount = data.get("pay_amount") or amount
            payin_extra_id = data.get("payin_extra_id")  # MEMO/tag for some chains
            payment_id = str(data.get("payment_id", ""))
            network_code = data.get("network", pay_currency)

            # Build QR for the payment address
            qr_data = pay_address
            if payin_extra_id:
                qr_data = f"{pay_address}?memo={payin_extra_id}"
            qr_url = (
                f"https://api.qrserver.com/v1/create-qr-code/"
                f"?size=200x200&data={qr_data}"
            ) if pay_address else None

            return PaymentOrderResult(
                success=True,
                order_id=payment_id,          # NOWPayments payment_id
                amount=amount,
                currency="USDT",
                crypto_address=pay_address,
                network=network_code,
                qr_url=qr_url,
                raw_response={
                    **data,
                    "pay_currency": pay_currency.lower(),
                    "pay_amount": str(pay_amount),
                    "payin_extra_id": payin_extra_id,
                    "payment_id": payment_id,
                },
            )

        except httpx.HTTPStatusError as exc:
            err_body = {}
            try:
                err_body = exc.response.json()
            except Exception:
                pass
            msg = err_body.get("message") or err_body.get("error") or str(exc)
            logger.error(f"NOWPayments create_order HTTP error {exc.response.status_code}: {msg}")
            return PaymentOrderResult(
                success=False,
                order_id="",
                amount=amount,
                currency=currency,
                error_message=f"NOWPayments API error: {msg}",
            )
        except Exception as exc:
            logger.error(f"NOWPayments create_order exception: {exc}")
            return PaymentOrderResult(
                success=False,
                order_id="",
                amount=amount,
                currency=currency,
                error_message=f"NOWPayments provider error: {str(exc)}",
            )

    def verify_webhook(
        self,
        headers: Dict[str, str],
        raw_body: bytes,
    ) -> WebhookResult:
        """
        Verify NOWPayments IPN signature.

        Per official docs:
          1. Parse JSON body, sort keys, re-serialize deterministically
          2. Compute HMAC-SHA512 of sorted JSON string with IPN secret
          3. Compare with x-nowpayments-sig header (constant-time)
        """
        if not self.ipn_secret:
            return WebhookResult(valid=False, error_message="IPN secret not configured")

        # Case-insensitive header lookup
        headers_lower = {k.lower(): v for k, v in headers.items()}
        incoming_sig = headers_lower.get("x-nowpayments-sig", "")
        if not incoming_sig:
            return WebhookResult(
                valid=False,
                error_message="Missing x-nowpayments-sig header",
            )

        # Step 1: parse and re-sort JSON
        try:
            payload = json.loads(raw_body.decode("utf-8"))
        except Exception:
            return WebhookResult(valid=False, error_message="Invalid JSON body")

        sorted_json = json.dumps(payload, sort_keys=True, separators=(",", ":"))

        # Step 2: HMAC-SHA512
        expected_sig = hmac.new(
            self.ipn_secret.encode("utf-8"),
            sorted_json.encode("utf-8"),
            hashlib.sha512,
        ).hexdigest()

        # Step 3: constant-time comparison
        if not hmac.compare_digest(expected_sig.lower(), incoming_sig.lower()):
            logger.warning(
                f"NOWPayments IPN signature mismatch. "
                f"incoming={incoming_sig[:16]}... expected={expected_sig[:16]}..."
            )
            return WebhookResult(valid=False, error_message="Signature verification failed")

        # Extract key fields
        payment_id = str(payload.get("payment_id", ""))
        order_id = str(payload.get("order_id", ""))
        status = payload.get("payment_status", "")
        actually_paid = payload.get("actually_paid") or payload.get("actually_paid_at_fiat")
        outcome_amount = payload.get("outcome_amount")
        pay_currency = payload.get("pay_currency", "")
        payin_hash = payload.get("payin_hash")

        # Credit amount: prefer outcome_amount (what we receive), fallback to actually_paid
        credit_amount = None
        raw_credit = outcome_amount or actually_paid
        if raw_credit is not None:
            try:
                credit_amount = Decimal(str(raw_credit))
            except Exception:
                credit_amount = None

        return WebhookResult(
            valid=True,
            order_id=order_id,                   # our internal reference
            provider_transaction_id=payment_id,  # NOWPayments payment_id
            utr=payin_hash,                      # on-chain tx hash
            amount=credit_amount,
            currency="USDT",
            status=status,
            payer_info=pay_currency,
            raw_payload=json.dumps(payload),
        )

    def verify_order_status(
        self,
        provider_order_id: str,
    ) -> PaymentVerificationResult:
        """
        Server-to-server poll of a payment's current status.
        provider_order_id is the NOWPayments payment_id (numeric string).
        """
        if not self.is_configured():
            return PaymentVerificationResult(
                verified=False,
                status="error",
                provider_order_id=provider_order_id,
                error_message="NOWPayments not configured",
            )
        try:
            data = self._api_get(f"/payment/{provider_order_id}")
            status = data.get("payment_status", "waiting")
            actually_paid = data.get("actually_paid", 0)
            outcome_amount = data.get("outcome_amount")
            payin_hash = data.get("payin_hash")

            raw_credit = outcome_amount or actually_paid
            credit_amount = None
            try:
                credit_amount = Decimal(str(raw_credit)) if raw_credit else None
            except Exception:
                pass

            verified = status in NOWPAYMENTS_SUCCESS_STATUSES and bool(
                credit_amount and credit_amount > 0
            )

            return PaymentVerificationResult(
                verified=verified,
                status=status,
                provider_order_id=provider_order_id,
                utr=payin_hash,
                amount=credit_amount,
                raw_response=data,
            )
        except Exception as exc:
            logger.error(f"NOWPayments verify_order_status error: {exc}")
            return PaymentVerificationResult(
                verified=False,
                status="error",
                provider_order_id=provider_order_id,
                error_message=str(exc),
            )

    # ------------------------------------------------------------------
    # Extra helpers exposed to router
    # ------------------------------------------------------------------

    def get_supported_networks(self) -> List[Dict[str, Any]]:
        """Returns the list of supported USDT networks for display in UI."""
        return [
            {
                "id":          n["id"],
                "label":       n["label"],
                "network":     n["network"],
                "min_usdt":    str(n["min_usdt"]),
                "icon":        n["icon"],
                "description": n["description"],
            }
            for n in USDT_NETWORKS
        ]

    @staticmethod
    def is_success_status(status: str) -> bool:
        return status in NOWPAYMENTS_SUCCESS_STATUSES

    @staticmethod
    def is_failed_status(status: str) -> bool:
        return status in NOWPAYMENTS_FAILED_STATUSES

    @staticmethod
    def is_pending_status(status: str) -> bool:
        return status in NOWPAYMENTS_PENDING_STATUSES
